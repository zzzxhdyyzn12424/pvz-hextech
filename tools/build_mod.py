"""Merge the mod into a full unpacked game tree and prepare a release.

What it does:
1. Copies work/unpacked -> build/main_unpack (never edits your source tree).
2. Updates properties/LawnStrings.txt with the names / almanac text from
   content/plants.csv, keeping every other string untouched.
3. Applies content/strings_extra.csv (extra [KEY] sections).
4. Validates that mapped parts exist and were actually replaced.
5. Optionally runs a pack command; otherwise prints PopStudio instructions.
6. Builds build/release/ + a zip with main.pak, README and preview.

Usage:
    python tools/build_mod.py --config config.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from pvz_common import (
    ROOT,
    die,
    has_cjk,
    load_config,
    read_rows,
    read_text_auto,
    reset_dir,
    safe_name,
    upsert_section,
    warn,
    write_text,
    zip_dir,
)

ACTIVE_STATUS = {"art", "coded", "done", "wip", "playable"}


def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


#: 中文版 main.pak 里 LawnStrings 的两种落点。
#: `qroperties` 是打包器把目录名首字母 +1 的结果，见 tools/pvz_pak.py 的说明。
LAWN_CANDIDATE_DIRS = ("properties", "qroperties")


def find_lawnstrings(unpacked: Path) -> Path:
    """定位 LawnStrings.txt。

    1.0.0.1051 CN 的 main.pak 里它叫 `qroperties\\LawnStrings.txt`（首字母被
    偏移了 1），而不是 `properties\\LawnStrings.txt`。两个都认，谁的段落更全用谁。
    """
    found = []
    for dirname in LAWN_CANDIDATE_DIRS:
        d = unpacked / dirname
        if not d.is_dir():
            continue
        for p in d.iterdir():
            if p.is_file() and p.name.lower() == "lawnstrings.txt":
                found.append(p)
    if not found:
        die(
            "找不到 LawnStrings.txt。已在以下位置查找：\n  "
            + "\n  ".join(str(unpacked / d / "LawnStrings.txt") for d in LAWN_CANDIDATE_DIRS)
            + "\n这个文件负责植物名和图鉴文本。确认解包目录是 main.pak 的内容。"
        )
    if len(found) == 1:
        return found[0]

    # 多份时选段落数最多的（中文版 pak 内常是英文残本）
    def section_count(p: Path) -> int:
        try:
            text, _ = read_text_auto(p)
        except Exception:
            return 0
        return sum(1 for line in text.splitlines() if line.strip().startswith("["))

    best = max(found, key=section_count)
    others = [p for p in found if p is not best]
    warn(
        "发现多份 LawnStrings.txt，使用 "
        f"{best.relative_to(unpacked)}（{section_count(best)} 段）"
        + "，忽略 " + ", ".join(str(p.relative_to(unpacked)) for p in others)
    )
    return best


def build_description(plant: dict) -> str:
    almanac = (plant.get("almanac") or "").strip()
    behavior = (plant.get("behavior") or "").strip()
    if "{" in almanac:
        return almanac
    lines = [almanac]
    if behavior:
        lines += ["{SHORTLINE}", "{KEYWORD}特性：{STAT}" + behavior]
    return "\n".join(line for line in lines if line != "")


def main() -> None:
    parser = argparse.ArgumentParser(description="合并内容并生成可打包的 main_unpack")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--skip-copy", action="store_true", help="复用已有的 build/main_unpack，加快迭代")
    parser.add_argument("--all", action="store_true", help="忽略 status，把所有企划都写入文本（方便先看效果）")
    args = parser.parse_args()

    cfg = load_config(args.config)
    unpacked = Path(cfg["unpacked_dir"])
    staging = Path(cfg["staging_dir"])
    if not unpacked.is_dir():
        die(f"找不到解包目录 {unpacked}")

    lawn_src = find_lawnstrings(unpacked)
    if not args.skip_copy:
        reset_dir(staging)
        shutil.copytree(
            unpacked,
            staging,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("*.bak", "__pycache__", ".DS_Store", "Thumbs.db"),
        )
        print(f"[+] 工作副本: {staging}")
    elif not staging.is_dir():
        die(f"--skip-copy 需要 {staging} 已存在")

    # 保留 LawnStrings 在 pak 内的原始目录名（1.0.0.1051 CN 是 qroperties）
    lawn = staging / lawn_src.parent.name / lawn_src.name
    text, encoding = read_text_auto(lawn)
    uses_crlf = "\r\n" in text

    plants = read_rows(ROOT / "content" / "plants.csv")
    parts = read_rows(ROOT / "content" / "parts.csv")
    texture_map = {r["part_file"] for r in read_rows(ROOT / "content" / "texture_map.csv")}

    if not plants:
        die("content/plants.csv 为空。")

    seen_slots: set[str] = set()
    active = []
    for plant in plants:
        slot = (plant.get("slot") or "").strip().lower()
        if not slot:
            continue
        if slot in seen_slots:
            die(f"plants.csv 里 slot 重复: {slot}")
        seen_slots.add(slot)
        if args.all or (plant.get("status") or "").strip().lower() in ACTIVE_STATUS:
            active.append(plant)

    if not active:
        warn("没有 status=art/coded/done 的植物，本次只会生成一个原样底包。")

    if "en" in (cfg.get("base_game") or "").lower() and any(
        has_cjk(p.get("new_name", "")) or has_cjk(p.get("almanac", "")) for p in active
    ):
        warn(
            "检测到中文文本，但 base_game 标记为英文版。\n"
            "    英文版默认字体可能不含中文字形，游戏里会显示方块/乱码。\n"
            "    解决：换中文底包（如人人网 1.1.0.1056），或使用字体补丁。"
        )

    for plant in active:
        slot = plant["slot"].strip().upper()
        new_name = (plant.get("new_name") or "").strip()
        if not new_name:
            warn(f"{slot} 没有 new_name，跳过文本替换")
            continue
        text = upsert_section(text, slot, new_name)
        text = upsert_section(text, f"{slot}_DESCRIPTION", build_description(plant))

    for row in read_rows(ROOT / "content" / "strings_extra.csv"):
        key = (row.get("key") or "").strip()
        if key:
            text = upsert_section(text, key, row.get("value", ""))

    # Keep the original newline style. Windows LawnStrings is usually CRLF;
    # mixing in LF-only sections is tolerated by most builds but not worth the risk.
    if uses_crlf:
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")

    write_text(lawn, text, encoding)
    print(f"[+] LawnStrings 已更新（编码 {encoding}），生效植物 {len(active)} 个")

    # Validate part mappings
    changed, unchanged, missing = [], [], []
    for row in parts:
        part_file = (row.get("part_file") or "").strip()
        if not part_file:
            continue
        target = staging / "reanim" / part_file
        backup = Path(cfg["backup_dir"]) / "reanim" / part_file
        if not target.exists():
            missing.append(part_file)
            continue
        if texture_map and part_file not in texture_map:
            warn(f"{part_file} 不在 texture_map.csv 里，可能写错了文件名")
        if backup.exists() and md5(target) != md5(backup):
            changed.append(part_file)
        else:
            unchanged.append(part_file)

    print(f"[+] 贴图：已替换 {len(changed)}，未变化 {len(unchanged)}，缺失 {len(missing)}")
    if missing:
        for name in sorted(set(missing))[:10]:
            print(f"    - reanim/{name}")
    if unchanged:
        print("    未变化的部件（可能还没跑 prepare_textures.py）：")
        for name in unchanged[:10]:
            print(f"    - {name}")

    preview = ROOT / "build" / "preview.png"
    pak = Path(cfg["output_pak"])

    # ---- 先打包，再收进 release。顺序反了会把上一次构建的旧 pak 发出去。
    cmd = cfg.get("pack_command") or []
    if cmd:
        values = dict(cfg)
        values.update(
            {
                "staging": str(staging),
                "output": str(pak),
                "mod_name": cfg["mod_name"],
                "version": cfg["mod_version"],
            }
        )
        command = [str(part).format(**values) for part in cmd]
        print("[+] 执行打包命令:", " ".join(command))
        # 旧 pak 必须清掉：打包失败时不能让 stale 文件冒充新产物
        if pak.exists():
            pak.unlink()
        subprocess.run(command, check=True, cwd=str(ROOT))
        if not pak.exists():
            die(f"打包命令跑完了但 {pak} 不存在，检查 pack_command")
    else:
        if pak.exists():
            warn(f"未配置 pack_command，沿用已存在的 {pak.name}（可能是旧包）")

    release = reset_dir(cfg["release_dir"])
    if pak.exists():
        shutil.copy2(pak, release / "main.pak")
        print(f"[+] main.pak -> {release / 'main.pak'}  ({pak.stat().st_size:,} 字节)")
    else:
        warn(
            "还没有 main.pak：确认 pack_command 能正常跑，或对 "
            f"{staging} 手动打包后放到 {pak}"
        )

    if preview.exists():
        shutil.copy2(preview, release / "preview.png")

    info = {
        "mod_name": cfg["mod_name"],
        "version": cfg["mod_version"],
        "base_game": cfg["base_game"],
        "preview_all": args.all,
        "plants": [
            {
                "slot": p["slot"],
                "name": p.get("new_name", ""),
                "status": p.get("status", ""),
                "behavior": p.get("behavior", ""),
            }
            for p in active
        ],
        "replaced_parts": sorted(changed),
        "built_from": str(unpacked),
    }
    (release / "mod_info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    banner = "\n（预览构建：包含未完成企划，请勿直接发布）" if args.all else ""
    readme = f"""{cfg['mod_name']} v{cfg['mod_version']}{banner}
========================================

适用底包：{cfg['base_game']}
文本植物：{len(active)} 个
替换贴图：{len(changed)} 个部件

安装方法
--------
1. 备份你游戏目录里的 main.pak。
2. 把本压缩包里的 main.pak 覆盖到游戏目录。
3. 启动 PlantsVsZombies.exe。

注意
----
- 本作品为免费同人改版，请勿售卖、内购或打包原版游戏本体。
- 如果启动黑屏/闪退，先换回备份的 main.pak，再检查底包版本是否匹配。
- 中文文本需要底包字体支持；英文底包可能显示方块。
- 详细内容见 mod_info.json。
"""
    (release / "README_安装说明.txt").write_text(readme, encoding="utf-8-sig")

    zip_path = release.parent / (
        f"{safe_name(cfg['mod_name'])}_v{cfg['mod_version']}"
        + ("_preview" if args.all else "")
        + ".zip"
    )
    if zip_path.exists():
        zip_path.unlink()
    zip_dir(release, zip_path)
    print(f"[+] 发布包: {zip_path}")


if __name__ == "__main__":
    main()
