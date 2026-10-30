# -*- coding: utf-8 -*-
"""
make_dllmod_release.py —— 打「只含 mod」的发布包（不含任何游戏资源）

产出：
    build/release_dllmod/PVZ海克斯版_dllmod_v1.7.0/
        wsock32.dll          ← 代理本体（我们自己的代码）
        一键部署.bat          ← 自动从玩家系统目录复制 wsock32_orig.dll
        使用说明.txt
    build/release_dllmod/PVZ海克斯版_dllmod_v1.7.0.zip

为什么只发这些：

    PlantsVsZombies.exe / main.pak / images / reanim / font / particles /
    data 全是 PopCap/EA 的版权素材，仓库 .gitignore 第一条就写着不入库。
    wsock32_orig.dll 是 Windows 系统文件副本，也不该由我们分发 ——
    所以部署脚本改成「从玩家自己的 C:\\Windows\\SysWOW64 复制」。
    玩家拿到的是几 MB 的 mod，不是 37M 的游戏本体。

用法：
    python tools/make_dllmod_release.py
    python tools/make_dllmod_release.py --version 1.7.0
"""

from __future__ import print_function

import argparse
import os
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DLLMOD = os.path.join(ROOT, "dllmod")
DIST = os.path.join(DLLMOD, "dist")
OUT = os.path.join(ROOT, "build", "release_dllmod")

DLL = os.path.join(DLLMOD, "out", "wsock32.dll")

# 包里放的静态文件（都在 dllmod/dist/ 下）
FILES = ["一键部署.bat", "使用说明.txt"]


def newest_source_mtime():
    """src/*.cpp|h|S 里最新的修改时间，用来判断 out/wsock32.dll 是不是过期了。"""
    newest = 0.0
    src = os.path.join(DLLMOD, "src")
    for name in os.listdir(src):
        if name.endswith((".cpp", ".h", ".S")):
            newest = max(newest, os.path.getmtime(os.path.join(src, name)))
    return newest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.7.0")
    args = ap.parse_args()

    if not os.path.isfile(DLL):
        print("[!] 没找到 %s，先跑 dllmod\\build.bat" % DLL)
        return 1

    # 过期检查：源码比 DLL 新，说明忘了重新编译 —— 这种包发出去就是旧行为
    if os.path.getmtime(DLL) < newest_source_mtime():
        print("[!] out\\wsock32.dll 比源码旧，先重新编译：dllmod\\build.bat")
        return 1

    for f in FILES:
        if not os.path.isfile(os.path.join(DIST, f)):
            print("[!] 缺少 dllmod\\dist\\%s" % f)
            return 1

    name = "PVZ海克斯版_dllmod_v%s" % args.version
    stage = os.path.join(OUT, name)
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)

    shutil.copy2(DLL, os.path.join(stage, "wsock32.dll"))
    for f in FILES:
        shutil.copy2(os.path.join(DIST, f), os.path.join(stage, f))

    zpath = os.path.join(OUT, name + ".zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in ["wsock32.dll"] + FILES:
            z.write(os.path.join(stage, f), name + "/" + f)

    total = sum(os.path.getsize(os.path.join(stage, f))
                for f in ["wsock32.dll"] + FILES)
    print("[+] %s" % stage)
    for f in ["wsock32.dll"] + FILES:
        print("      %-20s %8d 字节" % (f, os.path.getsize(os.path.join(stage, f))))
    print("[+] %s  (%.2f MB)" % (zpath, os.path.getsize(zpath) / 1048576.0))
    print("[+] 合计 %.2f MB（不含任何游戏资源）" % (total / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
