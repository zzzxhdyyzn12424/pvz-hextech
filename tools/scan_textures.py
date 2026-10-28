"""Scan an unpacked main.pak and record every reanim part texture.

Usage:
    python tools/scan_textures.py --unpacked work/unpacked

Output:
    content/texture_map.csv

The output is the source of truth for target sizes. prepare_textures.py uses it
to resize AI art to the exact dimensions the game expects.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    raise SystemExit("需要 Pillow。先运行: pip install -r requirements.txt")

from pvz_common import ROOT, die, warn, write_rows

FIELDS = [
    "part_file",
    "width",
    "height",
    "bbox_left",
    "bbox_top",
    "bbox_right",
    "bbox_bottom",
    "plant_guess",
]


def guess_plant(stem: str) -> str:
    return stem.split("_", 1)[0].lower()


def main() -> None:
    parser = argparse.ArgumentParser(description="扫描解包后的 reanim 贴图并生成尺寸表")
    parser.add_argument("--unpacked", default=str(ROOT / "work" / "unpacked"), help="main.pak 解包目录")
    parser.add_argument("--out", default=str(ROOT / "content" / "texture_map.csv"), help="输出 CSV")
    args = parser.parse_args()

    unpacked = Path(args.unpacked).resolve()
    reanim = unpacked / "reanim"
    if not reanim.is_dir():
        die(
            f"找不到 {reanim}\n"
            "先用 PopStudio 解包 main.pak，把内容放到 work/unpacked/ 下。"
        )

    rows: list[dict] = []
    errors = 0
    for png in sorted(reanim.glob("*.png")):
        try:
            with Image.open(png) as im:
                im.load()
                width, height = im.size
                if "A" not in im.getbands():
                    im = im.convert("RGBA")
                bbox = im.getchannel("A").getbbox()
                if bbox is None:
                    bbox = (0, 0, width, height)
                rows.append(
                    {
                        "part_file": png.name,
                        "width": width,
                        "height": height,
                        "bbox_left": bbox[0],
                        "bbox_top": bbox[1],
                        "bbox_right": bbox[2],
                        "bbox_bottom": bbox[3],
                        "plant_guess": guess_plant(png.stem),
                    }
                )
        except Exception as exc:  # noqa: BLE001 - keep scanning the rest
            warn(f"读不了 {png.name}: {exc}")
            errors += 1

    if not rows:
        die(f"{reanim} 里没有 PNG。确认解包目录结构是 reanim/*.png。")

    out = Path(args.out)
    write_rows(out, rows, FIELDS)

    total_px = sum(int(r["width"]) * int(r["height"]) for r in rows)
    biggest = sorted(rows, key=lambda r: int(r["width"]) * int(r["height"]), reverse=True)[:5]
    print(f"[+] 扫描到 {len(rows)} 个贴图，写入 {out}")
    print(f"    总像素约 {total_px / 1_000_000:.1f} MP，读取失败 {errors} 个")
    print("    最大的 5 个：")
    for r in biggest:
        print(f"      {r['part_file']:<42} {r['width']}x{r['height']}")
    print("\n下一步：打开 content/parts.csv，把 AI 图对应的 part_file 填进去。")


if __name__ == "__main__":
    main()
