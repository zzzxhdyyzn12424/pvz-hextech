#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fieldscan.py —— 统计某段位移区间内、非索引寻址的字段访问（读/写分开）

为什么需要它：
  arrayhist.py 只认「基址 + 索引*4 + 位移」的数组访问。
  Board 自己的标量字段（波次、阳光、场景…）都是 [ebp+0x5570] 这种
  纯「基址+位移」形态，arrayhist 一条都扫不到。

  但找「波次计数」恰恰要靠这个统计：
    - 波次字段：整个 exe 里只被写几次（每波一次），读也少
    - 阳光字段：每帧都在被写（-1/+N）
    - 坐标/时间：每帧读写
  所以「写次数极少的 dword 字段」就是候选。

用法：
    python fieldscan.py --min 0x5540 --max 0x5640
    python fieldscan.py --min 0x5540 --max 0x5640 --disp 0x5568 --near 10
"""

import argparse
import collections
import os
import struct
import sys

try:
    import capstone
except ImportError:
    raise SystemExit("需要 capstone:  .venv\\Scripts\\python.exe -m pip install capstone")

DEFAULT_EXE = r"F:\pvz\Plants_Vs_Zombies_V1.0.0.1051_CN_V2\PlantsVsZombies.exe"

# 这些助记符的第一个操作数是内存，但语义上是「读」
READONLY_MNEMONICS = {
    "cmp", "test", "push", "call", "jmp", "je", "jne", "ja", "jb", "jae", "jbe",
    "jg", "jl", "jge", "jle", "js", "jns", "jo", "jno", "jp", "jnp",
    "movzx", "movsx", "fld", "fst", "fstp", "div", "idiv", "mul", "imul",
    "bt", "inc", "dec", "add", "sub", "and", "or", "xor", "shl", "shr", "sar",
}


def load_sections(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] != b"MZ":
        raise SystemExit("不是 PE 文件")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise SystemExit("PE 签名不对")
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    optsz = struct.unpack_from("<H", data, pe + 20)[0]
    base = struct.unpack_from("<I", data, pe + 0x34)[0]
    sec0 = pe + 24 + optsz
    out = []
    for i in range(nsec):
        o = sec0 + i * 40
        name = data[o:o + 8].rstrip(b"\0").decode("latin1")
        vsize, va, rsize, roff = struct.unpack_from("<4I", data, o + 8)
        sz = min(vsize, rsize) or rsize
        out.append(dict(name=name, va=base + va, size=sz, data=data[roff:roff + sz]))
    return base, out


def is_write(ins):
    """第一个操作数是内存 => 写（cmp/test/push 之类除外）"""
    ops = ins.operands
    if not ops:
        return False
    if ops[0].type != capstone.x86.X86_OP_MEM:
        return False
    mn = ins.mnemonic
    if mn in ("cmp", "test", "push", "call", "jmp", "movzx", "movsx"):
        return False
    if mn.startswith("j"):
        return False
    # add/sub/and/or/xor 同时读写，算「写」
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=DEFAULT_EXE)
    ap.add_argument("--min", default="0x5540")
    ap.add_argument("--max", default="0x5640")
    ap.add_argument("--top", type=int, default=200)
    ap.add_argument("--disp", default=None, help="只看这个位移的详细访问点")
    ap.add_argument("--near", type=int, default=0, help="配合 --disp 打上文")
    ap.add_argument("--size", type=int, default=0,
                    help="只保留这个宽度的字段（1/2/4），0=全部")
    ap.add_argument("--range", default=None,
                    help="反汇编一个地址区间，如 0x415B00-0x415BC0")
    ap.add_argument("--callers", default=None,
                    help="找出所有 call/jmp 到某个地址的位置")
    ap.add_argument("--imm", default=None,
                    help="找出所有带某个立即数的指令，如 --imm 0x14c（找 imul 步长）")
    ap.add_argument("--mnem", default=None,
                    help="配合 --imm 过滤助记符，如 --mnem imul")
    args = ap.parse_args()

    lo = int(args.min, 16)
    hi = int(args.max, 16)
    if not os.path.isfile(args.exe):
        raise SystemExit("找不到 exe: %s" % args.exe)

    base, secs = load_sections(args.exe)
    text = [s for s in secs if s["name"] == ".text"]
    if not text:
        raise SystemExit("没有 .text 节")
    t = text[0]

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    md.skipdata = True

    # ---- 模式 I：找带某个立即数的指令 ----
    if args.imm:
        want = int(args.imm, 16)
        n = 0
        for ins in md.disasm(t["data"], t["va"]):
            if ins.id == 0:
                continue
            if args.mnem and ins.mnemonic != args.mnem:
                continue
            hit = any(op.type == capstone.x86.X86_OP_IMM and op.imm == want
                      for op in ins.operands)
            if hit:
                n += 1
                print("  0x%08X  %s %s" % (ins.address, ins.mnemonic, ins.op_str))
        print("\n共 %d 条指令带立即数 0x%X" % (n, want))
        return 0

    # ---- 模式 C：找调用者 ----
    if args.callers:
        want = int(args.callers, 16)
        n = 0
        for ins in md.disasm(t["data"], t["va"]):
            if ins.id == 0:
                continue
            if not ins.mnemonic.startswith(("call", "jmp", "j")):
                continue
            if not ins.operands or ins.operands[0].type != capstone.x86.X86_OP_IMM:
                continue
            if ins.operands[0].imm == want:
                n += 1
                print("  0x%08X  %s %s" % (ins.address, ins.mnemonic, ins.op_str))
        print("\n共 %d 处引用 0x%X" % (n, want))
        return 0

    # ---- 模式 R：反汇编地址区间 ----
    if args.range:
        a, b = args.range.split("-")
        a, b = int(a, 16), int(b, 16)
        off = a - t["va"]
        n = 0
        for ins in md.disasm(t["data"][off:], a):
            if ins.address >= b:
                break
            if ins.id == 0:
                print("  %08X  (data)" % ins.address)
                continue
            print("  %08X  %s %s" % (ins.address, ins.mnemonic, ins.op_str))
            n += 1
        print("\n%d 条指令" % n)
        return 0

    if args.disp is not None:
        want = int(args.disp, 16)
        recent = collections.deque(maxlen=max(1, args.near) + 1)
        n = 0
        for ins in md.disasm(t["data"], t["va"]):
            if ins.id == 0:
                continue
            tx = "%s %s" % (ins.mnemonic, ins.op_str)
            hit = any(op.type == capstone.x86.X86_OP_MEM and op.mem.disp == want
                      and op.mem.index == 0 for op in ins.operands)
            if hit:
                n += 1
                kind = "WRITE" if is_write(ins) else "read "
                print("\n--- #%d [%s] @ 0x%08X  %s" % (n, kind, ins.address, tx))
                for a, s in recent:
                    print("      0x%08X  %s" % (a, s))
                print("  >>> 0x%08X  %s" % (ins.address, tx))
            recent.append((ins.address, tx))
        print("\n共 %d 处访问 [xxx + 0x%X]" % (n, want))
        return 0

    wr = collections.Counter()
    rd = collections.Counter()
    fsize = {}
    wsample = collections.defaultdict(list)
    rsample = collections.defaultdict(list)
    n_ins = 0
    for ins in md.disasm(t["data"], t["va"]):
        n_ins += 1
        if ins.id == 0:
            continue
        tx = "%s %s" % (ins.mnemonic, ins.op_str)
        seen = set()
        for op in ins.operands:
            if op.type != capstone.x86.X86_OP_MEM:
                continue
            m = op.mem
            if m.index != 0:
                continue
            d = m.disp
            if d < lo or d > hi or d in seen:
                continue
            seen.add(d)
            fsize[d] = op.size
            if is_write(ins):
                wr[d] += 1
                if len(wsample[d]) < 3:
                    wsample[d].append((ins.address, tx))
            else:
                rd[d] += 1
                if len(rsample[d]) < 3:
                    rsample[d].append((ins.address, tx))

    print("扫描 %d 条指令，区间 [0x%X, 0x%X]" % (n_ins, lo, hi))
    print("\n%-9s %4s %5s %5s  %s" % ("位移", "宽", "写", "读", "写样例"))
    rows = sorted(set(wr) | set(rd), key=lambda d: (wr[d], rd[d]))
    shown = 0
    for d in rows:
        if args.size and fsize.get(d) != args.size:
            continue
        if shown >= args.top:
            break
        shown += 1
        s = wsample[d][0] if wsample[d] else rsample[d][0]
        print("+0x%05X  %4d %5d %5d  %08X  %s"
              % (d, fsize.get(d, 0), wr[d], rd[d], s[0], s[1]))
    print("\n按写次数升序排列 —— 写 0~2 次的 dword 才可能是「波次」这种低频字段")
    return 0


if __name__ == "__main__":
    sys.exit(main())
