"""体检 reanim 贴图：替换前后逐部件对比，出可机读 + 可人读的报告。

为什么需要这个工具
------------------
`prepare_textures.py` 只负责"把图画进去"，它不知道图画得对不对。
肉眼盯 1873 个部件不现实，于是这里把肉眼能看出来的问题做成量化指标：

    canvas      画布尺寸有没有被改（改了动画就会错位）
    bbox        图形包围盒是否偏离原版（偏了就是"头不在脖子上"）
    coverage    可见像素占比，暴露出"填得太满 / 缩得太小"
    transparent 完全透明像素数，暴露"图形变胖"
    magenta     洋红/绿幕残留，暴露抠图没抠干净
    edge        画布四边贴边像素，暴露溢出被硬裁
    semi        半透明边缘占比，过高说明缩放用了低质量算法或边缘糊
    md5         与备份逐字节相同 = 这个部件其实没被替换

严重度
------
    FAIL  必须修，否则游戏里一定难看/错位
    WARN  大概率难看，建议修
    INFO  仅供参考

用法
----
    python tools/verify_textures.py --config config.json
    python tools/verify_textures.py --config config.json --only peashooter
    python tools/verify_textures.py --config config.json --report build/verify.json
    python tools/verify_textures.py --config config.json --sheet build/verify.png

退出码
------
    0  没有 FAIL
    1  存在 FAIL（可直接接进 CI / run.bat）
"""
from __future__ import annotations

import argparse
import json
import hashlib
from dataclasses import dataclass, field, asdict
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np
except ImportError:
    raise SystemExit("需要 Pillow 和 numpy。先运行: pip install -r requirements.txt")

from pvz_common import ROOT, die, load_config, read_rows, warn


# ---------------------------------------------------------------------------
# 阈值：全部集中在这里，好调
# ---------------------------------------------------------------------------

# 包围盒四条边允许的最大偏移（像素）。原版 bbox 是动画锚点，偏太多就会脱位。
BBOX_TOL = 3
# bbox 中心允许的最大偏移，比单边更贴近"视觉上歪没歪"
CENTER_TOL = 2.5
# 可见像素占比的相对变化容忍度（0.25 = 允许 ±25%）
COVERAGE_TOL = 0.25
# 半透明像素占可见像素的比例上限，超了说明边缘发毛
SEMI_MAX = 0.35
# 洋红/绿幕残留的绝对像素数上限（超过就报）
MAGENTA_MAX = 8
# 新图贴边、原版不贴边 -> 溢出裁剪。允许原版本来就贴的那条边
EDGE_TOL = 0


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class PartMetrics:
    """一个贴图的全部测量值，前后各算一份。"""
    width: int
    height: int
    bbox: tuple[int, int, int, int] | None
    visible: int          # alpha > 0
    transparent: int      # alpha == 0
    semi: int             # 0 < alpha < 255
    opaque: int           # alpha == 255
    magenta: int          # 洋红/绿幕残留像素
    edges: dict[str, int] # 四边贴边像素
    md5: str = ""

    @property
    def bbox_w(self) -> int:
        return (self.bbox[2] - self.bbox[0]) if self.bbox else 0

    @property
    def bbox_h(self) -> int:
        return (self.bbox[3] - self.bbox[1]) if self.bbox else 0

    @property
    def coverage(self) -> float:
        """可见像素 / 画布面积。"""
        total = self.width * self.height
        return self.visible / total if total else 0.0

    @property
    def semi_ratio(self) -> float:
        return self.semi / self.visible if self.visible else 0.0

    def center(self) -> tuple[float, float] | None:
        if not self.bbox:
            return None
        return ((self.bbox[0] + self.bbox[2]) / 2, (self.bbox[1] + self.bbox[3]) / 2)


@dataclass
class Finding:
    level: str      # FAIL / WARN / INFO
    part: str
    plant: str
    code: str
    msg: str

    def line(self) -> str:
        return f"[{self.level}] {self.part} ({self.plant}) {self.code}: {self.msg}"


@dataclass
class PartReport:
    part: str
    plant: str
    status: str
    replaced: bool
    before: PartMetrics | None = None
    after: PartMetrics | None = None
    findings: list[Finding] = field(default_factory=list)
    paths: tuple[str, str] | None = None

    @property
    def worst(self) -> str:
        levels = {f.level for f in self.findings}
        for lv in ("FAIL", "WARN", "INFO"):
            if lv in levels:
                return lv
        return "OK"


# ---------------------------------------------------------------------------
# 测量
# ---------------------------------------------------------------------------

def md5_of(path: Path) -> str:
    h = hashlib.md5()
    h.update(path.read_bytes())
    return h.hexdigest()


def measure(path: Path) -> PartMetrics:
    with Image.open(path) as src:
        im = src.convert("RGBA")
    a = np.asarray(im).astype(np.int16)
    rgb, alpha = a[..., :3], a[..., 3]
    h, w = alpha.shape

    visible_mask = alpha > 0
    # 检测残留时只看"肉眼能看见"的像素。Lanczos 等插值会在全透明区域边缘
    # 留下 alpha=1 的孤像素，它的 RGB 可能是任意离谱的值（实测出现过
    # (0,255,0,1)），完全不可见却会污染计数。alpha>=16 基本等价于可视阈值。
    residue_mask = alpha >= 16
    bbox = im.getchannel("A").getbbox()

    # 洋红残留：R 和 B 都高、G 低。这是洋红底抠图的失败特征，可靠。
    #
    # 口径必须同时带上"R 和 B 明显高于 G"这个相对条件。
    # 只写绝对阈值 (R>140 & B>140 & G<140) 会把灰白色的半透明像素也算进来
    # （实测口水是 (169,127,152)，R/G/B 三者接近，看着是灰紫白，根本不是洋红）。
    # 真正的洋红是 R、B 远高于 G，差值至少在 60 以上。
    g = rgb[..., 1]
    mag = (
        residue_mask
        & (rgb[..., 0] > 140)
        & (rgb[..., 2] > 140)
        & (g < 140)
        & ((np.minimum(rgb[..., 0], rgb[..., 2]) - g) > 60)
    )
    # 注意：不要用"G 高、R/B 低"去检测绿幕残留。
    # 豌豆/坚果这类绿色植物本身就会大面积命中，产生海量误报（实测 1829 像素）。
    # 绿幕抠图失败的特征是"纯饱和绿"且色彩方差极低，用更严的口径：
    green = (
        residue_mask
        & (rgb[..., 1] > 200)      # 接近 #00FF00 那种亮绿
        & (rgb[..., 0] < 60)
        & (rgb[..., 2] < 60)
    )

    edges = {
        "top": int((alpha[0, :] > 0).sum()),
        "bottom": int((alpha[h - 1, :] > 0).sum()),
        "left": int((alpha[:, 0] > 0).sum()),
        "right": int((alpha[:, w - 1] > 0).sum()),
    }

    return PartMetrics(
        width=w,
        height=h,
        bbox=bbox,
        visible=int(visible_mask.sum()),
        transparent=int((alpha == 0).sum()),
        semi=int(((alpha > 0) & (alpha < 255)).sum()),
        opaque=int((alpha == 255).sum()),
        magenta=int(mag.sum()) + int(green.sum()),
        edges=edges,
        md5=md5_of(path),
    )


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------

def compare(part: str, plant: str, status: str,
            before: PartMetrics, after: PartMetrics) -> list[Finding]:
    out: list[Finding] = []

    # 1) 画布尺寸绝不能变
    if (before.width, before.height) != (after.width, after.height):
        out.append(Finding(
            "FAIL", part, plant, "CANVAS",
            f"画布尺寸变了 {before.width}x{before.height} -> {after.width}x{after.height}，动画会错位"
        ))

    # 2) 图形包围盒
    if after.bbox is None:
        out.append(Finding("FAIL", part, plant, "EMPTY", "替换后是全透明的，游戏里这个部件会消失"))
        return out
    if before.bbox is not None:
        deltas = [abs(after.bbox[i] - before.bbox[i]) for i in range(4)]
        worst = max(deltas)
        if worst > BBOX_TOL:
            out.append(Finding(
                "WARN", part, plant, "BBOX",
                f"包围盒偏移 {deltas}（>{BBOX_TOL}px）：原 {before.bbox} -> 新 {after.bbox}"
            ))
        cb, ca = before.center(), after.center()
        if cb and ca:
            shift = ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5
            if shift > CENTER_TOL:
                out.append(Finding(
                    "WARN", part, plant, "CENTER",
                    f"图形中心偏移 {shift:.1f}px（>{CENTER_TOL}）：( {cb[0]:.1f},{cb[1]:.1f} ) -> ( {ca[0]:.1f},{ca[1]:.1f} )"
                ))

    # 3) 填充度：最直观的"变胖/变瘦"
    if before.coverage > 0:
        ratio = after.coverage / before.coverage
        if ratio > 1 + COVERAGE_TOL:
            out.append(Finding(
                "WARN", part, plant, "TOO_FAT",
                f"填充度 {before.coverage:.1%} -> {after.coverage:.1%}（{ratio:.2f}x），图形变胖，"
                f"透明区 {before.transparent} -> {after.transparent}"
            ))
        elif ratio < 1 - COVERAGE_TOL:
            out.append(Finding(
                "WARN", part, plant, "TOO_THIN",
                f"填充度 {before.coverage:.1%} -> {after.coverage:.1%}（{ratio:.2f}x），图形偏小"
            ))

    # 4) 抠图残留
    if after.magenta > MAGENTA_MAX:
        out.append(Finding(
            "FAIL", part, plant, "KEY_RESIDUE",
            f"洋红/绿幕残留 {after.magenta} 像素（上限 {MAGENTA_MAX}）：调大 --tolerance 到 60-80 重跑"
        ))
    elif after.magenta > 0:
        out.append(Finding(
            "INFO", part, plant, "KEY_RESIDUE",
            f"洋红/绿幕残留 {after.magenta} 像素（阈值内，但肉眼可能看到淡边）"
        ))
    elif before.magenta > MAGENTA_MAX and after.magenta == 0:
        out.append(Finding(
            "INFO", part, plant, "KEY_CLEAN", "原版本身带背景色残留，替换后已清干净"
        ))

    # 5) 溢出裁剪：新图贴边而原版不贴边
    clipped = [
        k for k in before.edges
        if before.edges[k] <= EDGE_TOL and after.edges[k] > EDGE_TOL
    ]
    if clipped:
        detail = ", ".join(f"{k} {before.edges[k]}->{after.edges[k]}" for k in clipped)
        out.append(Finding(
            "WARN", part, plant, "EDGE_CLIP",
            f"图形被画布裁切（{detail}）：缩小 scale 或给素材留安全边距"
        ))

    # 6) 半透明边缘
    if after.semi_ratio > SEMI_MAX:
        out.append(Finding(
            "WARN", part, plant, "SOFT_EDGE",
            f"半透明像素占可见 {after.semi_ratio:.1%}（上限 {SEMI_MAX:.0%}）：边缘发毛，"
            f"检查抠图或缩放质量"
        ))

    # 7) 压根没替换
    if before.md5 == after.md5:
        out.append(Finding(
            "INFO", part, plant, "NOT_REPLACED",
            f"与备份逐字节相同，这个部件实际没被替换（status={status}）"
        ))

    return out


# ---------------------------------------------------------------------------
# 字体：报告里有中文，PIL 默认位图字体全是方块
# ---------------------------------------------------------------------------

_FONT_CACHE: dict[int, "ImageFont.ImageFont | None"] = {}

# 按优先级找 CJK 字体。第一条命中的就用。
_CJK_FONTS = [
    r"C:\Windows\Fonts\msyh.ttc",       # 微软雅黑
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",     # 黑体
    r"C:\Windows\Fonts\Deng.ttf",       # 等线
    r"C:\Windows\Fonts\simsun.ttc",     # 宋体
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def get_font(size: int = 12):
    """拿一个能显示中文的字体；全都没有就返回 None（调用方退化为默认字体）。"""
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    font = None
    for path in _CJK_FONTS:
        p = Path(path)
        if p.exists():
            try:
                font = ImageFont.truetype(str(p), size)
                break
            except Exception:
                continue
    _FONT_CACHE[size] = font
    return font


# ---------------------------------------------------------------------------
# 拼图：给眼睛看的
# ---------------------------------------------------------------------------

def build_sheet(rows: list[PartReport], out: Path, max_items: int = 40) -> None:
    items = [r for r in rows if r.before and r.after][:max_items]
    if not items:
        warn("没有可拼图的部件")
        return

    cell_w, cell_h = 260, 224
    cols = min(4, len(items))
    rows_n = (len(items) + cols - 1) // cols
    sheet = Image.new("RGBA", (cols * cell_w, rows_n * cell_h), (246, 244, 238, 255))
    draw = ImageDraw.Draw(sheet)

    f_small = get_font(11)
    f_norm = get_font(12)
    f_bold = get_font(13)

    level_color = {
        "FAIL": (200, 40, 40, 255),
        "WARN": (220, 140, 20, 255),
        "INFO": (90, 120, 200, 255),
        "OK": (40, 150, 70, 255),
    }

    # 色条底：从等级色取 RGB 往白里调，保证文字对比度
    level_rgb = {
        "FAIL": (200, 40, 40),
        "WARN": (220, 140, 20),
        "INFO": (90, 120, 200),
        "OK": (40, 150, 70),
    }

    def text_xy(xy, s, font, fill):
        """有中文字体就用，没有就退回默认字体（至少不崩）。"""
        if font is None:
            draw.text(xy, s, fill=fill)
        else:
            draw.text(xy, s, fill=fill, font=font)

    for idx, rep in enumerate(items):
        col, r = idx % cols, idx // cols
        ox, oy = col * cell_w, r * cell_h
        draw.rectangle([ox, oy, ox + cell_w - 1, oy + cell_h - 1], outline=(216, 212, 202, 255))

        for slot, path, label in (
            (0, rep.paths[0], "BEFORE"),
            (1, rep.paths[1], "AFTER"),
        ):
            try:
                with Image.open(path) as src:
                    thumb = src.convert("RGBA")
                thumb.thumbnail((104, 118), Image.LANCZOS)
                px = ox + 12 + slot * 120 + (104 - thumb.width) // 2
                py = oy + 12 + (118 - thumb.height) // 2
                sheet.alpha_composite(thumb, (px, py))
            except Exception:
                pass
            text_xy((ox + 12 + slot * 120, oy + 134), label, f_small, (90, 84, 76, 255))

        color = level_color.get(rep.worst, (20, 18, 16, 255))
        band = tuple(int(c + (255 - c) * 0.84) for c in level_rgb.get(rep.worst, (20, 18, 16)))
        draw.rectangle([ox + 10, oy + 152, ox + cell_w - 10, oy + 172],
                       fill=band + (255,), outline=color)
        text_xy((ox + 14, oy + 155), f"{rep.worst}  {rep.part[:26]}", f_bold, color)

        # 只显示最严重的那一条，避免卡片糊成一片
        if rep.findings:
            top = sorted(rep.findings, key=lambda f: ("FAIL", "WARN", "INFO").index(f.level))[0]
            text_xy((ox + 12, oy + 180), top.code, f_norm, (60, 58, 54, 255))
            text_xy((ox + 12, oy + 198), top.msg[:28], f_small, (110, 104, 96, 255))
            text_xy((ox + 12, oy + 212), top.msg[28:56], f_small, (110, 104, 96, 255))
        else:
            text_xy((ox + 12, oy + 180), "通过", f_norm, (40, 150, 70, 255))

    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(out)
    print(f"[+] 体检拼图: {out}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="体检 reanim 贴图替换质量")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--only", default="", help="只体检某个 plant_id")
    parser.add_argument("--report", default="", help="把 JSON 报告写到这个路径")
    parser.add_argument("--sheet", default="", help="生成可视化拼图到这个路径")
    parser.add_argument("--quiet", action="store_true", help="只输出汇总，不逐条列明细")
    parser.add_argument("--all", action="store_true",
                        help="体检 unpacked 里全部 reanim PNG（不只 parts.csv 里登记的）")
    args = parser.parse_args()

    cfg = load_config(args.config)
    unpacked = Path(cfg["unpacked_dir"])
    reanim = unpacked / "reanim"
    backup_root = Path(cfg["backup_dir"]) / "reanim"
    if not reanim.is_dir():
        die(f"找不到 {reanim}，先解包 main.pak。")
    if not backup_root.is_dir():
        die(f"找不到备份 {backup_root}。体检必须要有原版参照，先跑一次 prepare_textures.py。")

    parts = read_rows(ROOT / "content" / "parts.csv")
    if not parts:
        die("content/parts.csv 为空。")

    if args.all:
        planted: dict[str, tuple[str, str]] = {}
        for row in parts:
            pf = row.get("part_file", "").strip()
            if pf:
                planted[pf] = (row.get("plant_id", ""), row.get("status", ""))
        todo = [
            {
                "plant_id": planted.get(p.name, ("unlisted", ""))[0],
                "part_file": p.name,
                "status": planted.get(p.name, ("", "unknown"))[1],
            }
            for p in sorted(backup_root.glob("*.png"))
        ]
    else:
        todo = [r for r in parts if r.get("part_file", "").strip()]

    if args.only:
        todo = [r for r in todo if r.get("plant_id", "") == args.only]
    if not todo:
        die("没有要体检的部件。")

    rows: list[PartReport] = []
    for row in todo:
        part = row["part_file"].strip()
        plant = row.get("plant_id", "") or "?"
        status = row.get("status", "")
        backup = backup_root / part
        target = reanim / part

        if not backup.exists():
            # 还没替换过的部件本来就没有备份，这是正常状态，不是问题。
            rows.append(PartReport(
                part=part, plant=plant, status=status, replaced=False,
                findings=[Finding("INFO", part, plant, "NOT_YET",
                                  "尚未替换（无备份，跳过对比）")],
            ))
            continue
        if not target.exists():
            rows.append(PartReport(
                part=part, plant=plant, status=status, replaced=False,
                findings=[Finding("FAIL", part, plant, "MISSING",
                                  f"unpacked/reanim/{part} 不存在")],
            ))
            continue

        before = measure(backup)
        after = measure(target)
        rep = PartReport(
            part=part, plant=plant, status=status,
            replaced=before.md5 != after.md5,
            before=before, after=after,
        )
        mode = (row.get("mode") or "").strip().lower()

        # mode=hide 的部件是「故意全透明」——AI 图已经把五官整颗画进 Head 里了，
        # 原版的嘴/唇/眨眼/眉毛必须让位，否则会叠成四只眼两张嘴。
        # 这类部件全透明是预期结果，不能报 FAIL，否则每跑一次体检就多 5 条假警报，
        # 真缺陷会被淹掉。
        if mode == "hide":
            if (before.width, before.height) != (after.width, after.height):
                rep.findings = [Finding(
                    "FAIL", part, plant, "CANVAS",
                    f"隐藏部件的画布尺寸变了 {before.width}x{before.height} -> "
                    f"{after.width}x{after.height}，reanim 锚点会错位"
                )]
            elif after.bbox is not None:
                rep.findings = [Finding(
                    "FAIL", part, plant, "HIDE_DIRTY",
                    "标记为 hide 但图里还有可见像素，会跟 Head 叠出来"
                )]
            else:
                rep.findings = [Finding(
                    "INFO", part, plant, "HIDDEN",
                    f"已按 mode=hide 清空（画布 {after.width}x{after.height} 未变）"
                )]
        else:
            rep.findings = compare(part, plant, status, before, after)
        rep.paths = (str(backup), str(target))
        rows.append(rep)

    # ---- 汇总 ----
    counts = {"FAIL": 0, "WARN": 0, "INFO": 0}
    for rep in rows:
        for f in rep.findings:
            counts[f.level] = counts.get(f.level, 0) + 1

    replaced = sum(1 for r in rows if r.replaced)
    print()
    print("=" * 72)
    print(f"体检 {len(rows)} 个部件，其中 {replaced} 个确实被替换过")
    print(f"FAIL {counts['FAIL']}   WARN {counts['WARN']}   INFO {counts['INFO']}")
    print("=" * 72)

    if not args.quiet:
        order = {"FAIL": 0, "WARN": 1, "INFO": 2}
        bad = sorted(
            [r for r in rows if r.findings],
            key=lambda r: (order.get(r.worst, 3), r.plant, r.part),
        )
        for rep in bad:
            for f in sorted(rep.findings, key=lambda x: order[x.level]):
                print(f.line())
        if not bad:
            print("全部干净，没有发现任何问题。")

    # ---- JSON 报告 ----
    if args.report:
        payload = {
            "summary": {
                "parts": len(rows),
                "replaced": replaced,
                **counts,
            },
            "parts": [
                {
                    "part": r.part,
                    "plant": r.plant,
                    "status": r.status,
                    "replaced": r.replaced,
                    "worst": r.worst,
                    "before": asdict(r.before) if r.before else None,
                    "after": asdict(r.after) if r.after else None,
                    "findings": [asdict(f) for f in r.findings],
                }
                for r in rows
            ],
        }
        rp = Path(args.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[+] JSON 报告: {rp}")

    # ---- 拼图 ----
    if args.sheet:
        build_sheet(rows, Path(args.sheet))

    raise SystemExit(1 if counts["FAIL"] else 0)


if __name__ == "__main__":
    main()
