"""补装 gcc-libs 运行库 + gettext 运行库（gcc.exe 依赖 libgcc_s_dw2-1.dll，as.exe 依赖 libintl-8.dll）"""
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
    "mingw-w64-i686-gcc-libs-16.1.0-7-any.pkg.tar.zst",   # 16.2.0-6 在镜像上是空壳
    "mingw-w64-i686-gettext-0.22.4-3-any.pkg.tar.zst",    # libintl-8.dll
]

for n in NAMES:
    out = DL / n
    if not out.exists() or out.stat().st_size < 1000:
        print("下载", n, flush=True)
        req = urllib.request.Request(BASE + n, headers={"User-Agent": "pvz-mod-kit/1.0"})
        data = urllib.request.urlopen(req, timeout=180).read()
        out.write_bytes(data)
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

print("\n检查关键 DLL:")
for d in ["libgcc_s_dw2-1.dll", "libintl-8.dll", "libstdc++-6.dll"]:
    p = DEST / "bin" / d
    print(("OK   " if p.exists() else "MISS ") + d)
