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
    lines.append("const HexDef kHexDefs[] = {")
    for d in C.HEX_DEFS:
        lines.append("    { %s, %s, %s, %s, %s, %s }," % (
            cstr(d["id"]), cstr(d["name"]), cstr(d["short"]),
            "Tier::" + d["tier"].capitalize(),
            "Scope::" + d["scope"].capitalize(),
            cstr(d.get("apply", "all")),
        ))
    lines.append("};")
    lines.append("")
    lines.append("const int kHexDefCount = %d;" % len(C.HEX_DEFS))
    lines.append("")
    lines.append("}  // namespace hx")

    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("写出 %s（%d 条词条）" % (out, len(C.HEX_DEFS)))


if __name__ == "__main__":
    main()
