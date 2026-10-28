#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_toolchain.py —— 从清华 MSYS2 镜像拉 i686 GCC，解压成可用的 32 位编译器

为什么不用 winget / 官方 github：
本机到 github.com 直连超时，winget 的 WinLibs 包也走 github release，
只有国内镜像（TUNA / npmmirror / aliyun）能通。

产物：
  behavior/toolchain/mingw32/bin/i686-w64-mingw32-g++.exe
  behavior/toolchain/mingw32/bin/i686-w64-mingw32-gcc.exe

用法：
  python behavior/fetch_toolchain.py
  python behavior/fetch_toolchain.py --check     # 只检查已装状态
"""
from __future__ import annotations

import argparse
import io
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

MIRROR = "https://mirrors.tuna.tsinghua.edu.cn/msys2/mingw/mingw32"

# 精确版本（取镜像上存在的最后一个 patch）
PKGS = [
    "mingw-w64-i686-gcc-16.2.0-6-any.pkg.tar.zst",
    "mingw-w64-i686-binutils-2.47-4-any.pkg.tar.zst",
    "mingw-w64-i686-gcc-libs-16.2.0-6-any.pkg.tar.zst",
    "mingw-w64-i686-libwinpthread-14.0.0.r98.g19f5121a2-1-any.pkg.tar.zst",
    "mingw-w64-i686-gmp-6.3.0-2-any.pkg.tar.zst",
    "mingw-w64-i686-mpfr-4.2.2-3-any.pkg.tar.zst",
    "mingw-w64-i686-mpc-1.4.1-1-any.pkg.tar.zst",
    "mingw-w64-i686-isl-0.28-1-any.pkg.tar.zst",
    "mingw-w64-i686-zlib-1.3.2-3-any.pkg.tar.zst",
    "mingw-w64-i686-zstd-1.5.7-2-any.pkg.tar.zst",
    "mingw-w64-i686-gettext-tools-1.0-1-any.pkg.tar.zst",
    "mingw-w64-i686-windows-default-manifest-20260815-1-any.pkg.tar.zst",
]

ROOT = Path(__file__).resolve().parent
TC = ROOT / "toolchain"
DL = TC / "_pkgs"
DEST = TC / "mingw32"

GCC = DEST / "bin" / "i686-w64-mingw32-g++.exe"


def log(msg: str) -> None:
    print(msg, flush=True)


def download(name: str) -> Path:
    out = DL / name
    if out.exists() and out.stat().st_size > 0:
        log(f"  [缓存] {name} ({out.stat().st_size/1024/1024:.1f} MB)")
        return out

    url = f"{MIRROR}/{name}"
    log(f"  [下载] {name}")
    req = urllib.request.Request(url, headers={"User-Agent": "pvz-mod-kit/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        buf = io.BytesIO()
        got = 0
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            buf.write(chunk)
            got += len(chunk)
            if total:
                pct = got * 100 // total
                print(f"\r         {pct:3d}%  {got/1024/1024:6.1f}/{total/1024/1024:.1f} MB",
                      end="", flush=True)
    print()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buf.getvalue())
    return out


def extract(pkg: Path) -> int:
    import zstandard

    with open(pkg, "rb") as f:
        dctx = zstandard.ZstdDecompressor()
        with dctx.stream_reader(f) as reader:
            data = reader.read()

    n = 0
    links: list[tuple[str, str]] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tf:
        members = []
        for m in tf.getmembers():
            # msys2 包内路径形如 mingw32/bin/xxx.exe
            parts = m.name.split("/")
            if len(parts) < 2 or parts[0] != "mingw32":
                continue

            rel = "/".join(parts[1:])
            if not rel:
                continue

            if m.issym() or m.islnk():
                # 软链在 Windows 上解不出来（c++.exe -> g++.exe 这种），
                # 稍后统一用硬拷贝重建。
                links.append((rel, m.linkname.split("/")[-1]))
                continue

            m.name = rel
            members.append(m)
            n += 1

        try:
            tf.extractall(DEST, members=members, filter="data")   # py3.12+
        except TypeError:
            tf.extractall(DEST, members=members)

    # 重建软链：目标在 DEST 里已经是真实文件，直接复制一份改名的
    for rel, target in links:
        src = DEST / target
        if not src.exists():
            # g++-16.2.0.exe 这类版本化的真实文件名，模糊找一下
            stem = Path(target).stem
            cands = sorted((DEST / "bin").glob(stem + "*"))
            if cands:
                src = cands[-1]
        dst = DEST / rel
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if args.check:
        if GCC.exists():
            log(f"[+] 工具链就绪: {GCC}")
            return 0
        log(f"[-] 缺 {GCC}")
        return 1

    try:
        import zstandard  # noqa: F401
    except ImportError:
        log("[!] 缺 zstandard，先跑: python -m pip install zstandard")
        return 1

    DL.mkdir(parents=True, exist_ok=True)
    DEST.mkdir(parents=True, exist_ok=True)

    log(f"[*] 镜像: {MIRROR}")
    log(f"[*] 目标: {DEST}")
    log(f"[*] {len(PKGS)} 个包")

    for name in PKGS:
        pkg = download(name)
        try:
            n = extract(pkg)
            log(f"        解压 {n} 个文件")
        except Exception as e:
            log(f"  [!] 解压失败 {name}: {e}")
            return 1

    if not GCC.exists():
        log(f"[!] 解压完但找不到 {GCC}")
        gccs = list(DEST.rglob("*-g++*"))
        log(f"    实际找到的 g++: {[str(p) for p in gccs[:5]]}")
        return 1

    log(f"\n[+] 完成: {GCC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
