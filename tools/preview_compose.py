#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把覆盖层按色键合成到游戏截图上，用于不开游戏时预览效果。

用法：
    python tools/preview_compose.py <游戏截图> <覆盖层png> <输出png> [左边框 上边框]
"""
import sys
from PIL import Image

KEY = (1, 2, 3)


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    game = Image.open(sys.argv[1]).convert("RGB")
    ov = Image.open(sys.argv[2]).convert("RGB")
    dx = int(sys.argv[4]) if len(sys.argv) > 4 else (game.width - ov.width) // 2
    dy = int(sys.argv[5]) if len(sys.argv) > 5 else (game.height - ov.height) // 2
    client = game.crop((dx, dy, dx + ov.width, dy + ov.height))

    # 色键 -> alpha 0
    px = ov.load()
    mask = Image.new("L", ov.size, 255)
    mp = mask.load()
    for y in range(ov.height):
        for x in range(ov.width):
            if px[x, y] == KEY:
                mp[x, y] = 0
    out = client.copy()
    out.paste(ov, (0, 0), mask)
    out.save(sys.argv[3])
    print("已合成 -> %s  (client 偏移 %d,%d)" % (sys.argv[3], dx, dy))
    return 0


if __name__ == "__main__":
    sys.exit(main())
