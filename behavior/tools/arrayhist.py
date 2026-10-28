#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arrayhist.py —— 从 .text 里统计「基址 + 索引*4 + 位移」的访问模式

为什么有用：
  PVZ 的 Board 里 mPlants / mZombies / mProjectiles 都是
  `XXX* mArr[MAX]` 形式的指针数组，编译出来必然是
      mov [ebx + ecx*4 + 0x1138], eax
  这种「基址寄存器 + 索引寄存器*4 + 常量位移」的寻址。
  把整个 .text 里所有这类指令的位移统计一遍，出现次数多的位移
  就是数组基址候选。完全静态，不需要游戏在跑。

用法：
    python arrayhist.py                 # 全部大位移，按次数排
    python arrayhist.py --min 0x100     # 只看位移 >= 0x100
    python arrayhist.py --exe F:\\pvz\\...\\PlantsVsZombies.exe
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


def load_sections(path):
    """极简 PE 解析：只要 .text 的 VA / 原始数据"""
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
        out.append(dict(name=name, va=base + va, size=min(vsize, rsize) or rsize,
                        roff=roff, data=data[roff:roff + (min(vsize, rsize) or rsize)]))
    return base, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=DEFAULT_EXE)
    ap.add_argument("--min", default="0x100", help="位移下限（含），默认 0x100")
    ap.add_argument("--max", default="0x60000", help="位移上限（含）")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--disp", default=None,
                    help="只列出访问这个位移的指令（任意寻址方式），如 --disp 0x138")
    ap.add_argument("--near", type=int, default=0,
                    help="配合 --disp：把命中点前 N 条指令也打出来看上下文")
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

    # ---- 模式 A：按位移检索（谁在访问 Board+0xNNN） ----
    if args.disp is not None:
        want = int(args.disp, 16)
        recent = collections.deque(maxlen=max(1, args.near) + 1)
        n_hit = 0
        for ins in md.disasm(t["data"], t["va"]):
            if ins.id == 0:
                continue
            text = "%s %s" % (ins.mnemonic, ins.op_str)
            hit = False
            idx_hit = False
            for op in ins.operands:
                if op.type == capstone.x86.X86_OP_MEM and op.mem.disp == want:
                    hit = True
                    if op.mem.index != 0:
                        idx_hit = True
            if hit:
                n_hit += 1
                print("\n--- 命中 #%d  @ 0x%08X  %s%s"
                      % (n_hit, ins.address, text,
                         "   ★带索引（就是数组访问）" if idx_hit else ""))
                for a, tx in recent:
                    print("      0x%08X  %s" % (a, tx))
                print("  >>> 0x%08X  %s" % (ins.address, text))
            recent.append((ins.address, text))
        print("\n共 %d 处访问 [xxx + 0x%X]" % (n_hit, want))
        return 0

    hits = collections.Counter()       # disp -> 次数
    samples = collections.defaultdict(list)   # disp -> [(addr, text)]
    n_ins = 0

    for ins in md.disasm(t["data"], t["va"]):
        n_ins += 1
        if ins.id == 0:          # SKIPDATA 产生的伪指令，没有操作数
            continue
        for op in ins.operands:
            if op.type != capstone.x86.X86_OP_MEM:
                continue
            m = op.mem
            if m.scale != 4 or m.index == 0:
                continue          # 只关心 索引*4 的形态
            d = m.disp
            if d < lo or d > hi:
                continue
            hits[d] += 1
            if len(samples[d]) < 3:
                samples[d].append((ins.address, "%s %s" % (ins.mnemonic, ins.op_str)))

    print("扫描指令 %d 条，命中 %d 处「基址+索引*4+位移」访问，位移去重 %d 个"
          % (n_ins, sum(hits.values()), len(hits)))
    print("\n--- 位移出现次数 TOP %d ---" % args.top)
    print("%-12s %6s   %s" % ("位移", "次数", "样例（地址 指令）"))
    for d, n in hits.most_common(args.top):
        s = samples[d][0]
        print("+0x%05X    %4d   %08X  %s" % (d, n, s[0], s[1]))
    print("\n提示：mPlants/mZombies/mProjectiles 三者位移差应是同一个数组长度（*4 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
