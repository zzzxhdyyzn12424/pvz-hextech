"""补装 libiconv（libintl-8.dll 依赖它）"""
import io
import tarfile
import urllib.request
import zstandard
from pathlib import Path

TC = Path(r"C:\Users\33854\Documents\默认项目\pvz-mod-kit\behavior\toolchain")
DEST = TC / "mingw32"
DL = TC / "_pkgs"
BASE = "https://mirrors.tuna.tsinghua.edu.cn/msys2/mingw/mingw32/"

# 先查镜像上有哪些 libiconv 版本
import re
html = urllib.request.urlopen(BASE, timeout=60).read().decode("utf-8", "ignore")
cands = sorted(set(re.findall(r"mingw-w64-i686-libiconv-[\d.]+-\d+-any\.pkg\.tar\.zst", html)))
print("可用 libiconv 包:", cands[-5:] if cands else "无")
if not cands:
    raise SystemExit("镜像上找不到 libiconv")

name = cands[-1]
out = DL / name
if not out.exists() or out.stat().st_size < 1000:
    print("下载", name)
    req = urllib.request.Request(BASE + name, headers={"User-Agent": "pvz-mod-kit/1.0"})
    out.write_bytes(urllib.request.urlopen(req, timeout=180).read())

raw = zstandard.ZstdDecompressor().stream_reader(open(out, "rb")).read()
cnt = 0
with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tf:
    members = []
    for m in tf.getmembers():
        parts = m.name.split("/")
        if len(parts) < 2 or parts[0] != "mingw32":
            continue
        rel = "/".join(parts[1:])
        if not rel or m.issym() or m.islnk():
            continue
        m.name = rel
        members.append(m)
        cnt += 1
    try:
        tf.extractall(DEST, members=members, filter="data")
    except TypeError:
        tf.extractall(DEST, members=members)
print("解压", cnt, "个文件")
print("libiconv-2.dll present:", (DEST / "bin" / "libiconv-2.dll").exists())
