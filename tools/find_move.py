#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
find_move.py —— 找僵尸移动代码

思路：僵尸的 mX 在 +0x08（int）。移动就是每帧改这个字段。
所以全 .text 里所有「写 [reg+0x08]」的指令都是候选，
再按地址范围（僵尸函数簇）和指令形态过滤。

用法：
    python find_move.py                 # 列出所有写 +0x08 的点
    python find_move.py --around 0x5317C0 --span 0x8000
                                        # 只看某个函数附近的
    python find_move.py --func 0x5317C0 # 反汇编包含该地址的函数
"""

import argparse
import struct
import sys

import capstone

DEFAULT_EXE = r"F:\pvz\Plants_Vs_Zombies_V1.0.0.1051_CN_V2\PlantsVsZombies.exe"

WRITE_MNEM = {
    "mov", "sub", "add", "inc", "dec", "and", "or", "xor",
    "movzx", "movsx", "sar", "shr", "shl",
}


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
            return data[off:off + size], va, size
    raise SystemExit("找不到 .text")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=DEFAULT_EXE)
    ap.add_argument("--disp", type=lambda s: int(s, 0), default=0x08)
    ap.add_argument("--min", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--max", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--around", type=lambda s: int(s, 0))
    ap.add_argument("--span", type=lambda s: int(s, 0), default=0x8000)
    ap.add_argument("--func", type=lambda s: int(s, 0))
    ap.add_argument("--limit", type=int, default=80)
    args = ap.parse_args()

    code, va, size = text_of(args.exe)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    md.skipdata = True

    lo, hi = args.min, args.max
    if args.around:
        lo, hi = args.around - args.span, args.around + args.span

    hits = []
    for ins in md.disasm(code, va):
        if ins.id == 0:
            continue
        a = ins.address
        if hi and not (lo <= a <= hi):
            continue
        if ins.mnemonic not in WRITE_MNEM:
            continue
        if not ins.operands:
            continue
        op0 = ins.operands[0]
        if op0.type != capstone.x86.X86_OP_MEM:
            continue
        if op0.mem.disp != args.disp:
            continue
        hits.append((a, "%s %s" % (ins.mnemonic, ins.op_str)))

    print("命中 %d 处写 [+0x%02X]" % (len(hits), args.disp))
    for a, s in hits[:args.limit]:
        print("  0x%08X  %s" % (a, s))
    if len(hits) > args.limit:
        print("  …还有 %d 处" % (len(hits) - args.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
