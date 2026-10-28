"""Turn AI-generated art into game-ready PvZ reanim part textures.

Workflow:
    assets/raw/<raw_file>  ->  remove background -> trim -> fit -> anchor
    -> overwrite work/unpacked/reanim/<part_file>

The target canvas size never changes, so the original reanim animation keeps
working. Placement is based on the original part's alpha bounding box, which
keeps heads on necks and mouths on faces.

Usage:
    python tools/prepare_textures.py --config config.json
    python tools/prepare_textures.py --config config.json --only peashooter --preview
"""
from __future__ import annotations

import argparse
import math
import shutil
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter
    import numpy as np
except ImportError:
    raise SystemExit("需要 Pillow 和 numpy。先运行: pip install -r requirements.txt")

from pvz_common import ROOT, die, load_config, read_rows, warn


# ---------------------------------------------------------------------------
# background removal
# ---------------------------------------------------------------------------

def _detect_key(rgb: np.ndarray, tolerance: int) -> np.ndarray | None:
    h, w = rgb.shape[:2]
    border = np.concatenate(
        [rgb[0, :, :], rgb[-1, :, :], rgb[:, 0, :], rgb[:, -1, :]], axis=0
    )
    candidates = [rgb[0, 0], rgb[0, w - 1], rgb[h - 1, 0], rgb[h - 1, w - 1]]
    best: tuple[float, np.ndarray] | None = None
    for candidate in candidates:
        dist = np.linalg.norm(border - candidate, axis=1)
        frac = float((dist < tolerance).mean())
        if best is None or frac > best[0]:
            best = (frac, candidate)
    if best is None or best[0] < 0.30:
        return None
    return best[1]


def remove_solid_background(im: Image.Image, tolerance: int = 48) -> Image.Image:
    """Remove a solid background connected to the image border.

    Flood-filling from the corners (instead of a global color key) keeps white
    eyes / teeth inside the character even when the background is white.
    """
    im = im.convert("RGBA")
    arr = np.asarray(im).astype(np.int16)
    rgb = arr[..., :3]
    alpha = arr[..., 3]
    h, w = rgb.shape[:2]

    key = _detect_key(rgb, tolerance)
    if key is None:
        return im

    dist = np.linalg.norm(rgb - key.astype(np.int16), axis=2)
    candidate = (dist < tolerance) & (alpha > 0)
    if not candidate.any():
        return im

    # .copy() is required: images created by fromarray() are read-only and
    # PIL's floodfill silently does nothing on them.
    mask_img = Image.fromarray((candidate * 255).astype(np.uint8), "L").copy()
    for seed in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if mask_img.getpixel(seed) == 255:
            ImageDraw.floodfill(mask_img, seed, 128, thresh=0)

    bg = np.array(mask_img) == 128
    if not bg.any():
        return im

    soft = np.clip((dist - tolerance * 0.6) / max(1.0, tolerance * 0.8), 0, 1)
    new_alpha = alpha.copy()
    new_alpha[bg] = 0
    edge = (~bg) & (dist < tolerance * 1.15)
    new_alpha[edge] = (alpha[edge] * soft[edge]).astype(np.int16)

    # Despill only on the soft edge, so the sprite's real colors stay intact.
    r, g, b = (int(v) for v in key)
    if r > 140 and b > 140 and g < 140:  # magenta
        rgb[edge, 0] = np.minimum(rgb[edge, 0], rgb[edge, 1])
        rgb[edge, 2] = np.minimum(rgb[edge, 2], rgb[edge, 1])
    elif g > 140 and r < 140 and b < 140:  # green
        rgb[edge, 1] = np.minimum(rgb[edge, 1], np.maximum(rgb[edge, 0], rgb[edge, 2]))

    arr[..., 3] = new_alpha
    arr[..., :3] = rgb
    arr[bg] = 0
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def trim_alpha(im: Image.Image) -> Image.Image:
    bbox = im.getchannel("A").getbbox()
    return im.crop(bbox) if bbox else im


def make_hidden_like(reference: Image.Image) -> Image.Image:
    """造一张「跟原部件同尺寸、但全透明」的图。

    用途：AI 图已经把眼睛/嘴/口水整颗画进 Head 里了，原版的 mouth / Lips /
    blink1 / blink2 / eyebrow 再叠上去就是四只眼两张嘴。
    把这些部件换成同尺寸全透明，等于让它们继续参与 reanim 动画（轨道、锚点、
    尺寸全不变），但什么也不显示。

    不能真换成 1x1：reanim 里每个部件有自己的锚点偏移，改尺寸会让
    整株的位置偏掉。
    """
    return Image.new("RGBA", reference.size, (0, 0, 0, 0))


def add_outline(im: Image.Image, width: int = 2, color=(32, 24, 20, 255)) -> Image.Image:
    if width <= 0:
        return im
    alpha = im.getchannel("A")
    expanded = alpha.filter(ImageFilter.MaxFilter(width * 2 + 1))
    outline = Image.new("RGBA", im.size, color)
    outline.putalpha(expanded)
    outline.alpha_composite(im)
    return outline


def fit_into(im: Image.Image, box_w: int, box_h: int, scale: float = 1.0) -> Image.Image:
    w, h = im.size
    if w <= 0 or h <= 0:
        return im
    ratio = min(box_w / w, box_h / h) * scale
    ratio = max(ratio, 0.01)
    return im.resize((max(1, round(w * ratio)), max(1, round(h * ratio))), Image.LANCZOS)


def clamp_scale(art: Image.Image, box_w: int, box_h: int, scale: float) -> tuple[Image.Image, float]:
    """放大后再裁回去，而不是让 composite 把边缘硬切掉。

    scale > 1 的本意是「让图形稍微超出包围盒、盖住原图边缘」，
    但如果直接交给 composite_at，超出部分会被画布裁掉，
    导致左右/上下黑边粗细不一致（实测 PeaShooter_Head 四边全部贴边）。
    这里改成：放大后按 bbox 中心居中裁回 box 尺寸，超出部分留在画布外由画布决定。
    """
    if scale <= 1.0:
        return fit_into(art, box_w, box_h, scale), scale
    big = fit_into(art, box_w, box_h, scale)
    # 裁回 box 尺寸（居中），这样图形四边等量溢出，不会单边贴死
    cx, cy = big.width // 2, big.height // 2
    left = cx - box_w // 2
    top = cy - box_h // 2
    return big.crop((left, top, left + box_w, top + box_h)), scale


def edge_touch_report(canvas: Image.Image) -> dict[str, int]:
    """统计画布四边贴边像素数，用于发现溢出裁剪。"""
    alpha = canvas.getchannel("A")
    w, h = canvas.size
    px = alpha.load()
    return {
        "top": sum(1 for x in range(w) if px[x, 0] > 0),
        "bottom": sum(1 for x in range(w) if px[x, h - 1] > 0),
        "left": sum(1 for y in range(h) if px[0, y] > 0),
        "right": sum(1 for y in range(h) if px[w - 1, y] > 0),
    }


def composite_at(canvas: Image.Image, art: Image.Image, x: int, y: int) -> None:
    """alpha_composite with clipping, so negative offsets do not crash."""
    left, top = max(0, x), max(0, y)
    right = min(canvas.width, x + art.width)
    bottom = min(canvas.height, y + art.height)
    if right <= left or bottom <= top:
        return
    crop = art.crop((left - x, top - y, right - x, bottom - y))
    canvas.alpha_composite(crop, (left, top))


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------

def make_preview(items: list[tuple[str, Image.Image, Image.Image]], out: Path) -> None:
    if not items:
        return
    cell_w, cell_h = 230, 190
    cols = min(4, len(items))
    rows = math.ceil(len(items) / cols)
    sheet = Image.new("RGBA", (cols * cell_w, rows * cell_h), (246, 244, 238, 255))
    draw = ImageDraw.Draw(sheet)

    for index, (name, original, new) in enumerate(items):
        col, row = index % cols, index // cols
        ox, oy = col * cell_w, row * cell_h
        draw.rectangle([ox, oy, ox + cell_w - 1, oy + cell_h - 1], outline=(220, 216, 206, 255))
        for slot, image, label in ((0, original, "BEFORE"), (1, new, "AFTER")):
            thumb = image.copy()
            thumb.thumbnail((96, 112), Image.LANCZOS)
            px = ox + 18 + slot * 108 + (96 - thumb.width) // 2
            py = oy + 18 + (112 - thumb.height) // 2
            sheet.alpha_composite(thumb, (px, py))
            draw.text((ox + 18 + slot * 108, oy + 136), label, fill=(90, 84, 76, 255))
        draw.text((ox + 14, oy + 158), name[:30], fill=(20, 18, 16, 255))

    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(out)
    print(f"[+] 预览图: {out}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="把 AI 原图处理成 reanim 分部件贴图")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--only", default="", help="只处理某个 plant_id，例如 peashooter")
    parser.add_argument("--outline", type=int, default=0, help="加描边，单位像素，建议 1-2")
    parser.add_argument("--tolerance", type=int, default=48, help="背景色容差，默认 48；背景不干净时调到 60-80")
    parser.add_argument("--preview", action="store_true", help="生成前后对比图")
    parser.add_argument("--strict", action="store_true", help="任何缺失都直接报错")
    args = parser.parse_args()

    cfg = load_config(args.config)
    unpacked = Path(cfg["unpacked_dir"])
    reanim = unpacked / "reanim"
    if not reanim.is_dir():
        die(f"找不到 {reanim}，先解包 main.pak。")

    texture_map = {r["part_file"]: r for r in read_rows(ROOT / "content" / "texture_map.csv")}
    if not texture_map:
        die("content/texture_map.csv 为空。先运行: python tools/scan_textures.py")

    parts = read_rows(ROOT / "content" / "parts.csv")
    if not parts:
        die("content/parts.csv 为空。先按示例填一行。")

    raw_dir = ROOT / "assets" / "raw"
    backup_dir = Path(cfg["backup_dir"])
    previews: list[tuple[str, Image.Image, Image.Image]] = []
    processed = 0
    missing: list[str] = []

    for row in parts:
        plant_id = row.get("plant_id", "")
        if args.only and plant_id != args.only:
            continue
        part_file = row.get("part_file", "").strip()
        raw_file = row.get("raw_file", "").strip()
        status = row.get("status", "").strip().lower()
        if not part_file:
            continue

        target = reanim / part_file

        # mode=hide：不看 raw_file，直接生成同尺寸全透明图盖上去。
        # 用来抹掉被 AI 图重复画过的叠加件（眼睛、嘴、眉）。
        if (row.get("mode") or "").strip().lower() == "hide":
            if not target.exists():
                missing.append(f"reanim/{part_file}")
                continue
            backup = backup_dir / "reanim" / part_file
            if not backup.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            with Image.open(backup) as src:
                original = src.convert("RGBA")
            canvas = make_hidden_like(original)
            canvas.save(target)
            if args.preview:
                previews.append((Path(part_file).stem + " [hide]", original, canvas.copy()))
            processed += 1
            print(f"[+] {part_file:<38} <- (隐藏/全透明)  ({canvas.width}x{canvas.height})")
            continue

        if not raw_file:
            continue

        raw_path = raw_dir / raw_file
        if not raw_path.exists():
            if status not in ("skip", "todo"):
                missing.append(f"assets/raw/{raw_file}")
            continue
        if not target.exists():
            missing.append(f"reanim/{part_file}")
            continue
        if part_file not in texture_map:
            warn(f"{part_file} 不在 texture_map.csv 里，确认文件名大小写是否正确")

        # Always use the pristine backup as the geometry reference, otherwise
        # running the script twice would measure the previous AI art and drift.
        backup = backup_dir / "reanim" / part_file
        if not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
        reference = backup if backup.exists() else target

        with Image.open(reference) as src:
            original = src.convert("RGBA")
        with Image.open(raw_path) as src:
            art = src.convert("RGBA")

        art = trim_alpha(remove_solid_background(art, args.tolerance))
        if art.width == 0 or art.height == 0:
            warn(f"{raw_file} 抠完是空的，跳过")
            continue
        if args.outline:
            art = add_outline(art, args.outline)

        original_bbox = original.getchannel("A").getbbox() or (0, 0, original.width, original.height)
        mode = (row.get("mode") or "center").strip().lower()
        if mode == "full":
            pad = 0.05
            box_w = max(1, int(original.width * (1 - 2 * pad)))
            box_h = max(1, int(original.height * (1 - 2 * pad)))
            center_x = original.width // 2 + int(row.get("offset_x") or 0)
            center_y = original.height // 2 + int(row.get("offset_y") or 0)
        else:
            box_w = max(1, original_bbox[2] - original_bbox[0])
            box_h = max(1, original_bbox[3] - original_bbox[1])
            center_x = (original_bbox[0] + original_bbox[2]) // 2 + int(row.get("offset_x") or 0)
            center_y = (original_bbox[1] + original_bbox[3]) // 2 + int(row.get("offset_y") or 0)
        try:
            scale = float(row.get("scale") or 1.0)
        except ValueError:
            scale = 1.0
        art, _ = clamp_scale(art, box_w, box_h, scale)

        canvas = Image.new("RGBA", original.size, (0, 0, 0, 0))
        composite_at(canvas, art, center_x - art.width // 2, center_y - art.height // 2)

        # 溢出检测：原版该边没有贴边像素，新版却有 -> 图形被画布硬切了
        orig_edges = edge_touch_report(original)
        new_edges = edge_touch_report(canvas)
        clipped = [k for k in orig_edges if orig_edges[k] == 0 and new_edges[k] > 0]
        if clipped:
            warn(
                f"{part_file} 四边溢出被裁剪：{','.join(clipped)}"
                f"（scale={scale}，左右/上下黑边会不一致；建议 scale<=1.0 或加 padding）"
            )

        canvas.save(target)

        if args.preview:
            previews.append((Path(part_file).stem, original, canvas.copy()))
        processed += 1
        print(f"[+] {part_file:<38} <- {raw_file}  ({canvas.width}x{canvas.height})")

    print(f"\n完成：替换 {processed} 个部件。")
    if missing:
        print("缺少（不会影响已处理的部件）：")
        for item in sorted(set(missing)):
            print(f"  - {item}")
        if args.strict:
            die("--strict 模式下有缺失，停止。")

    if args.preview:
        make_preview(previews, ROOT / "build" / "preview.png")


if __name__ == "__main__":
    main()
