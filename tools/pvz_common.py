"""Shared helpers for the PvZ texture-swap starter kit.

Only standard library here, so scripts can import it before Pillow is installed.
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def log(msg: str = "") -> None:
    print(msg)


def warn(msg: str) -> None:
    print(f"[!] {msg}")


def die(msg: str) -> None:
    raise SystemExit(f"[x] {msg}")


def load_config(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    if not p.exists():
        die(
            f"找不到配置文件：{p}\n"
            "先复制 config.example.json 为 config.json，再改成你的路径。"
        )
    cfg = json.loads(p.read_text(encoding="utf-8-sig"))
    base = p.parent
    for key in ("unpacked_dir", "staging_dir", "output_pak", "release_dir", "backup_dir"):
        value = cfg.get(key)
        if value:
            q = Path(value)
            if not q.is_absolute():
                cfg[key] = str((base / q).resolve())
    # Defaults
    cfg.setdefault("mod_name", "未命名改版")
    cfg.setdefault("mod_version", "0.1.0")
    cfg.setdefault("base_game", "1.0.0.1051-en")
    cfg.setdefault("unpacked_dir", str(ROOT / "work" / "unpacked"))
    cfg.setdefault("staging_dir", str(ROOT / "build" / "main_unpack"))
    cfg.setdefault("output_pak", str(ROOT / "build" / "main.pak"))
    cfg.setdefault("release_dir", str(ROOT / "build" / "release"))
    cfg.setdefault("backup_dir", str(ROOT / "work" / "backup"))
    cfg.setdefault("pack_command", [])
    return cfg


def read_rows(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        return [
            {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(f)
        ]


def write_rows(path: str | Path, rows: list[dict], fieldnames: list[str]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_text_auto(path: str | Path) -> tuple[str, str]:
    """Read a game text file and remember its encoding.

    LawnStrings differs between English and Chinese builds (utf-8 vs gbk),
    so never hard-code utf-8 here. A BOM is preserved only when the original
    file actually had one; adding a BOM to a plain UTF-8 file can break PVZ's
    section parser.
    """
    data = Path(path).read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    for enc in ("utf-8", "gbk", "cp1252", "latin-1"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace"), "latin-1"


def write_text(path: str | Path, text: str, encoding: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding=encoding, newline="") as f:
        f.write(text)


def upsert_section(text: str, key: str, value: str) -> str:
    """Replace a [KEY] section in LawnStrings, or append it if missing."""
    key = key.strip().upper().strip("[]")
    value = value.strip("\r\n")
    block = f"[{key}]\n{value}\n"
    pattern = re.compile(rf"(?ms)^\[{re.escape(key)}\][ \t]*\r?\n.*?(?=^\[|\Z)")
    if pattern.search(text):
        return pattern.sub(lambda _m: block + "\n", text, count=1)
    return text.rstrip("\r\n") + "\n\n" + block


def reset_dir(path: str | Path) -> Path:
    p = Path(path)
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def zip_dir(src: str | Path, out_zip: str | Path) -> Path:
    src = Path(src)
    out_zip = Path(out_zip)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in src.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(src))
    return out_zip


def safe_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")
    return cleaned or "mod"


def has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)
