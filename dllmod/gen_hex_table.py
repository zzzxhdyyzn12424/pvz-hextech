#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_hex_table.py —— 把 Python 的词条表导出成 C++ 头文件

单一事实来源仍然是 tools/hextech_core.py 的 HEX_DEFS。
这里只做「翻译」，不复制逻辑：改词条只改 Python，然后重新生成。

产出：gen/hex_table.gen.h
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import hextech_core as C  # noqa: E402


def cstr(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main():
    out = os.path.join(HERE, "gen", "hex_table.gen.h")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    lines = []
    lines.append("// 自动生成，别手改。改 tools/hextech_core.py 的 HEX_DEFS，")
    lines.append("// 然后跑 dllmod\\gen_hex_table.py 重新生成。")
    lines.append("#pragma once")
    lines.append("")
    lines.append('#include "hex.h"')
    lines.append("")
    lines.append("namespace hx {")
    lines.append("")
    # ---- 枚举：把每条词条编成一个编译期常量 ----
    # 为什么要这个：引擎每帧要对每个对象问十几次「你有没有某条词条」。
    # 用字符串问就要 48 次 strcmp，100Hz × 几十个对象 = 每秒几十万次，
    # 全花在比较字符串上。编成 int 之后 has() 只是在 1~3 个整数里找一下。
    lines.append("enum HexId : int {")
    for i, d in enumerate(C.HEX_DEFS):
        lines.append("    HEX_%s = %d," % (d["id"], i))
    lines.append("    HEX_COUNT")
    lines.append("};")
    lines.append("")
    lines.append("inline const char* const kHexIdNames[] = {")
    for d in C.HEX_DEFS:
        lines.append("    %s," % cstr(d["id"]))
    lines.append("};")
    lines.append("")
    lines.append("inline const HexDef kHexDefs[] = {")
    for d in C.HEX_DEFS:
        lines.append("    { %s, %s, %s, %s, %s, %s }," % (
            cstr(d["id"]), cstr(d["name"]), cstr(d["short"]),
            "Tier::" + d["tier"].capitalize(),
            "Scope::" + d["scope"].capitalize(),
            cstr(d.get("apply", "all")),
        ))
    lines.append("};")
    lines.append("")
    lines.append("inline const int kHexDefCount = %d;" % len(C.HEX_DEFS))
    lines.append("")

    # ---- 互斥组 ----
    # 同一只僵尸身上不能同时出现组内两条。最要紧的是移速那组：
    # 移速是加法叠加的，四条一起吃就是 ×6 以上，僵尸直接飞过去。
    groups = getattr(C, "EXCLUSIVE_GROUPS", [])
    width = max([len(g) for g in groups] + [1])
    lines.append("constexpr int kExclWidth = %d;" % width)
    lines.append("inline const int kExclGroups[][kExclWidth] = {")
    for g in groups:
        ids = ["HEX_" + x for x in sorted(g)]
        ids += ["-1"] * (width - len(ids))
        lines.append("    { %s }," % ", ".join(ids))
    lines.append("};")
    lines.append("inline const int kExclGroupCount = %d;" % len(groups))
    lines.append("")
    lines.append("}  // namespace hx")

    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("写出 %s（%d 条词条）" % (out, len(C.HEX_DEFS)))


if __name__ == "__main__":
    main()
