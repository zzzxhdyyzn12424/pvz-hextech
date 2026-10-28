"""对抗测试：验证 verify_textures 的检测器能抓到已知缺陷。

这是工具自证的一部分。检测器最大的风险不是"抓不到"，而是"抓到假的"——
所以每一张坏图都对应一个预期检查项，跑完对照。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_textures import measure, compare  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "work" / "backup" / "reanim" / "PeaShooter_Head.png"
CASES = ROOT / "build" / "_bbtest"

# 坏图 -> (说明, 必须命中的检查项集合)
EXPECT = {
    "bad_magenta.png": ("洋红没抠净", {"KEY_RESIDUE"}),
    "bad_empty.png": ("全透明", {"EMPTY"}),
    "bad_canvas.png": ("画布尺寸变了", {"CANVAS"}),
    "bad_shift.png": ("图形偏移", {"CENTER", "BBOX"}),
    "bad_fat.png": ("填满变胖", {"TOO_FAT"}),
    "bad_thin.png": ("缩太小", {"TOO_THIN"}),
}


def main() -> int:
    if not REF.exists():
        print("[x] 找不到参照图，先跑一次 prepare_textures.py")
        return 2
    before = measure(REF)

    print(f"{'坏图':<20}{'说明':<14}{'判定':<8}命中项")
    print("-" * 96)
    failed = 0
    for name, (desc, must) in EXPECT.items():
        p = CASES / name
        if not p.exists():
            print(f"{name:<20}{desc:<14}{'SKIP':<8}文件不存在")
            failed += 1
            continue
        after = measure(p)
        findings = compare(name, "peashooter", "todo", before, after)
        codes = {f.code for f in findings}
        worst = "FAIL" if any(f.level == "FAIL" for f in findings) else (
            "WARN" if any(f.level == "WARN" for f in findings) else "OK"
        )
        missing = must - codes
        mark = "OK  " if not missing else "MISS"
        if missing:
            failed += 1
        print(f"{name:<20}{desc:<14}{worst:<8}{mark} {','.join(sorted(codes))}")

    print("-" * 96)
    if failed:
        print(f"[x] {failed} 个用例没抓到预期缺陷")
        return 1
    print(f"[+] {len(EXPECT)} 个用例全部命中预期缺陷，检测器有效")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
