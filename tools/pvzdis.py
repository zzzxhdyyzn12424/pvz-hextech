#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dis.py —— 反汇编指定地址区间（PVZ 逆向常用）

用法：
    python tools/dis.py 0x524A70                  # 从该地址往下 200 条
    python tools/dis.py 0x524A70 --n 80
    python tools/dis.py 0x52AC80 --n 60 --span 0  # 精确看一段
    python tools/dis.py 0x524A70 --filter 0x34    # 只看碰 [..+0x34] 的指令
    python tools/dis.py 0x524A70 --callers        # 反查谁调了这个函数

用项目自带的 .venv（里面有 capstone）。
"""

import argparse
import struct
import sys

import capstone

DEFAULT_EXE = r"F:\pvz\Plants_Vs_Zombies_V1.0.0.1051_CN_V2\PlantsVsZombies.exe"


def load(path):
    data = open(path, "rb").read()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    optsz = struct.unpack_from("<H", data, pe + 20)[0]
    base = struct.unpack_from("<I", data, pe + 0x34)[0]
    sec0 = pe + 24 + optsz
    secs = []
    for i in range(nsec):
        o = sec0 + i * 40
        name = data[o:o + 8].rstrip(b"\0").decode("latin1")
        vsize, va, rsize, roff = struct.unpack_from("<4I", data, o + 8)
        secs.append((name, base + va, rsize, roff))
    return data, base, secs


def text_of(path):
    data, base, secs = load(path)
    for name, va, size, off in secs:
        if name == ".text":
            return data[off:off + size], va, size, data
    raise SystemExit("找不到 .text")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("addr", type=lambda s: int(s, 0))
    ap.add_argument("--exe", default=DEFAULT_EXE)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--filter", type=lambda s: int(s, 0), default=None,
                    help="只显示操作数里含该 disp 的指令，如 0x34")
    ap.add_argument("--callers", action="store_true",
                    help="全 .text 反查 call 到该地址的地方")
    ap.add_argument("--span", type=int, default=0x200,
                    help="--callers 时只扫该地址前后 span 字节")
    args = ap.parse_args()

    code, va, size, whole = text_of(args.exe)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    md.skipdata = True

    if args.callers:
        lo, hi = args.addr - args.span, args.addr + args.span
        hits = []
        for ins in md.disasm(code, va):
            if ins.id == 0:
                continue
            if not (lo <= ins.address <= hi):
                continue
            if ins.mnemonic != "call" or not ins.operands:
                continue
            op = ins.operands[0]
            if op.type == capstone.x86.X86_OP_IMM and op.imm == args.addr:
                hits.append(ins.address)
        print("调用 0x%08X 的地方：%d 处" % (args.addr, len(hits)))
        for a in hits:
            print("  0x%08X" % a)
        return 0

    start = args.addr - va
    if start < 0 or start >= size:
        raise SystemExit("地址不在 .text 内")
    buf = code[start:start + 0x1000]
    shown = 0
    for ins in md.disasm(buf, args.addr):
        if shown >= args.n:
            break
        line = "0x%08X  %-8s %s" % (ins.address, ins.mnemonic, ins.op_str)
        if args.filter is not None:
            has = any(o.type == capstone.x86.X86_OP_MEM
                      and o.mem.disp == args.filter for o in ins.operands)
            if not has:
                continue
        print(line)
        shown += 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
