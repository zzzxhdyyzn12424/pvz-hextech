#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
alloc_sites.py —— 找出所有 `push <大小>; call operator new` 的分配点

为什么：
  抛射物一定是 new 出来的，new 的大小就是 sizeof(Projectile)。
  把所有分配点的「大小 → 调用地址」列出来，按大小分组，
  就能反推每种游戏对象多大、由哪个函数创建。
  完全静态，不需要游戏在跑，也不用抓时机。

用法：
    python alloc_sites.py                # 列出全部，按大小分组
    python alloc_sites.py --size 0x1F4   # 只看某个大小的调用点
    python alloc_sites.py --fn 0x520000  # 反汇编某个调用点所在函数附近
"""

import argparse
import collections
import os
import struct
import sys

try:
    import capstone
except ImportError:
    raise SystemExit("需要 capstone")

DEFAULT_EXE = r"F:\pvz\Plants_Vs_Zombies_V1.0.0.1051_CN_V2\PlantsVsZombies.exe"
OPERATOR_NEW = 0x0061C130


def load_text(path):
    with open(path, "rb") as f:
        data = f.read()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    optsz = struct.unpack_from("<H", data, pe + 20)[0]
    base = struct.unpack_from("<I", data, pe + 0x34)[0]
    sec0 = pe + 24 + optsz
    for i in range(nsec):
        o = sec0 + i * 40
        name = data[o:o + 8].rstrip(b"\0").decode("latin1")
        vsize, va, rsize, roff = struct.unpack_from("<4I", data, o + 8)
        if name == ".text":
            size = min(vsize, rsize) or rsize
            return base, base + va, data[roff:roff + size]
    raise SystemExit("没有 .text")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=DEFAULT_EXE)
    ap.add_argument("--size", default=None, help="只列这个大小（十六进制）")
    ap.add_argument("--range", default=None,
                    help="只看这个地址区间的调用点，如 --range 0x51E000-0x522000")
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()
    if not os.path.isfile(args.exe):
        raise SystemExit("找不到 exe: %s" % args.exe)

    base, text_va, text = load_text(args.exe)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    md.skipdata = True

    # 逐条走，维护「最近一条 push 的立即数」
    last_push = None          # (addr, value)
    sites = []                # (call_addr, size, push_addr)
    for ins in md.disasm(text, text_va):
        if ins.id == 0:
            last_push = None
            continue
        if ins.mnemonic == "push" and ins.operands and \
                ins.operands[0].type == capstone.x86.X86_OP_IMM:
            last_push = (ins.address, ins.operands[0].imm & 0xFFFFFFFF)
            continue
        if ins.mnemonic == "call" and ins.operands and \
                ins.operands[0].type == capstone.x86.X86_OP_IMM:
            tgt = ins.operands[0].imm & 0xFFFFFFFF
            if tgt == OPERATOR_NEW and last_push and ins.address - last_push[0] <= 24:
                sites.append((ins.address, last_push[1], last_push[0]))
        if ins.mnemonic not in ("nop", "mov", "lea", "test", "cmp", "xor", "add", "sub"):
            last_push = None

    print("找到 %d 处 `push <大小>; call operator new(0x%X)`" % (len(sites), OPERATOR_NEW))
    by_size = collections.defaultdict(list)
    for call_a, size, push_a in sites:
        by_size[size].append((call_a, push_a))

    want = int(args.size, 16) if args.size else None
    if args.range:
        lo_s, hi_s = args.range.split("-")
        lo, hi = int(lo_s, 16), int(hi_s, 16)
        sel = [(c, s) for c, s, _ in sites if lo <= c < hi]
        print("\n--- 地址 0x%X..0x%X 内的分配点（%d 个）---" % (lo, hi, len(sel)))
        for c, s in sorted(sel):
            print("  %08X  大小 0x%X" % (c, s))
        return 0

    print("\n%-10s %-6s %s" % ("大小", "次数", "调用点"))
    for size, lst in sorted(by_size.items(), key=lambda kv: -len(kv[1]))[:args.top]:
        if want is not None and size != want:
            continue
        addrs = " ".join("%08X" % a for a, _ in lst[:8])
        print("0x%-8X %-6d %s" % (size, len(lst), addrs))
    print("\n合计不同大小 %d 种" % len(by_size))
    print("提示：sizeof(Plant) / sizeof(Zombie) / sizeof(Projectile) 都会出现在这张表里")
    return 0


if __name__ == "__main__":
    sys.exit(main())
