"""补装 mingw-w64 的 CRT 与头文件（链接时缺 crt2.o / libmingwex 等）"""
import io
import tarfile
import urllib.request
import zstandard
from pathlib import Path

TC = Path(r"C:\Users\33854\Documents\默认项目\pvz-mod-kit\behavior\toolchain")
DEST = TC / "mingw32"
DL = TC / "_pkgs"
BASE = "https://mirrors.tuna.tsinghua.edu.cn/msys2/mingw/mingw32/"

NAMES = [
    "mingw-w64-i686-crt-git-13.0.0.r91.gfc0b67305-1-any.pkg.tar.zst",
    "mingw-w64-i686-headers-14.0.0.r98.g19f5121a2-1-any.pkg.tar.zst",
    "mingw-w64-i686-winpthreads-git-12.0.0.r747.g1a99f8514-1-any.pkg.tar.zst",
]

for n in NAMES:
    out = DL / n
    if not out.exists() or out.stat().st_size < 1000:
        print("下载", n, flush=True)
        req = urllib.request.Request(BASE + n, headers={"User-Agent": "pvz-mod-kit/1.0"})
        out.write_bytes(urllib.request.urlopen(req, timeout=180).read())
    else:
        print("缓存", n, out.stat().st_size, flush=True)

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
    print("  解压", cnt, "个文件", flush=True)

print("\n关键文件检查:")
for p in ["i686-w64-mingw32/lib/crt2.o",
          "i686-w64-mingw32/lib/libmingwex.a",
          "i686-w64-mingw32/lib/libmsvcrt.a",
          "lib/default-manifest.o"]:
    print(("OK   " if (DEST / p).exists() else "MISS ") + p)
