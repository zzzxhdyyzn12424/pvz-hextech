"""拼图排版压力测试：用真实+坏图混合出一张多卡片图，检查排版。

单卡片看不出排版问题（换行、跨列、文字溢出），这里手工塞 8 条记录，
其中包含不同等级，验证格子对位和颜色区分。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_textures import PartReport, Finding, build_sheet, measure  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "work" / "backup" / "reanim" / "PeaShooter_Head.png"
NEW = ROOT / "work" / "unpacked" / "reanim" / "PeaShooter_Head.png"
BAD = ROOT / "build" / "_bbtest"


def main() -> None:
    if not REF.exists():
        print("[x] 缺参照图")
        return

    specs = [
        ("PeaShooter_Head.png", "OK", []),
        ("PeaShooter_mouth.png", "FAIL", [("FAIL", "KEY_RESIDUE", "洋红残留 1829 像素")]),
        ("SunFlower_head.png", "WARN", [("WARN", "TOO_FAT", "填充度 21.3% -> 46.8%（2.20x），图形变胖")]),
        ("Wallnut_body.png", "INFO", [("INFO", "NOT_YET", "尚未替换")]),
        ("PotatoMine_body.png", "WARN", [("WARN", "EDGE_CLIP", "图形被画布裁切")]),
        ("Wallnut_cracked1.png", "FAIL", [("FAIL", "CANVAS", "画布尺寸变了 100x100 -> 100x88，动画会错位")]),
        ("PotatoMine_eyes.png", "WARN", [("WARN", "SOFT_EDGE", "半透明像素占可见 52.1%")]),
        ("PeaShooter_Lips.png", "OK", []),
    ]

    m_before = measure(REF)
    m_after = measure(NEW if NEW.exists() else REF)

    rows = []
    for i, (part, _worst, fs) in enumerate(specs):
        # 用不同图片制造视觉差异，模拟真实场景
        src = NEW if i % 3 == 0 else REF
        rep = PartReport(
            part=part, plant="demo", status="todo", replaced=True,
            before=m_before, after=m_after,
            findings=[Finding(lv, part, "demo", code, msg) for lv, code, msg in fs],
            paths=(str(REF), str(src)),
        )
        rows.append(rep)

    out = ROOT / "build" / "_sheet_test.png"
    build_sheet(rows, out)
    print(f"[+] {out}  等级={[r.worst for r in rows]}")


if __name__ == "__main__":
    main()
