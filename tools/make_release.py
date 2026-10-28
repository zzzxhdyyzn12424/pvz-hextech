# -*- coding: utf-8 -*-
"""
make_release.py —— 生成可以发给别人的发布包

产出（默认在 build/release_hextech/ 下）：

    PVZ海克斯版_v1.3.0/
        PVZ海克斯版.exe          ← 双击就能用，玩家不需要装 Python
        使用说明.txt
        版本要求.txt
    PVZ海克斯版_v1.3.0.zip

    PVZ海克斯版_v1.3.0_源码版/
        tools/launcher.py 等
        启动.bat
        使用说明.txt
    PVZ海克斯版_v1.3.0_源码版.zip

为什么两个包都发：

    exe 版零门槛，但「读别人进程内存」这个行为会被部分杀软当成木马
    （PyInstaller 打包的程序尤其容易被误报）。
    源码版给被杀软拦下的玩家兜底：只要装了 Python 就能跑，行为完全透明。

用法：

    python tools/make_release.py                # 打包
    python tools/make_release.py --no-exe       # 只出源码版（没装 pyinstaller 时）
    python tools/make_release.py --version 1.3.1
"""

import json
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

MOD_NAME = "PVZ海克斯版"
DEFAULT_VERSION = "1.3.0"

BUILD_ROOT = os.path.join(ROOT, "build", "release_hextech")
DIST_DIR = os.path.join(BUILD_ROOT, "dist")
WORK_DIR = os.path.join(BUILD_ROOT, "work")
SPEC_DIR = os.path.join(BUILD_ROOT, "spec")

# 源码版要带上的文件（相对 ROOT）
SRC_FILES = [
    "tools/launcher.py",
    "tools/version_check.py",
    "tools/hextech.py",
    "tools/hextech_core.py",
    "docs/hextech_values.md",
    "docs/hextech_design.md",
    "requirements.txt",
]

# 打包版需要显式声明的模块（launcher 里是运行时 import）
HIDDEN = ["hextech", "hextech_core", "version_check"]


# ---------------------------------------------------------------- 文案
def readme_text(version, is_src):
    head = """================================================================
  PVZ 海克斯版 v%s
================================================================

一个给《植物大战僵尸》加「海克斯强化」词条的同人 mod。
每波给你 1~3 个随机词条，植物和僵尸都会变强，效果直接作用在游戏里。

※ 本 mod 不包含游戏本体。你需要自己准备《植物大战僵尸》。


----------------------------------------------------------------
  一、你需要什么
----------------------------------------------------------------

  · 植物大战僵尸 1.0.0.1051 中文版
      - 右键 PlantsVsZombies.exe → 属性 → 详细信息，看「文件版本」
      - 文件大小应约为 3,007,800 字节
  · Windows 7 及以上

  版本不对会怎样？启动器会直接拦住你，并告诉你哪里不对。
  这不是刁难：本 mod 是直接读写游戏内存实现的，偏移量必须与版本严格对应，
  版本不对时强行运行会改错内存，可能让游戏崩溃或存档损坏。


----------------------------------------------------------------
  二、怎么用
----------------------------------------------------------------

  1. 先打开游戏，随便进一关（让场上出现植物和僵尸）
  2. %s
  3. 看到提示「版本校验通过」就好了
  4. 回到游戏里正常玩，植物和僵尸头上会出现词条卡片

  默认热键：

      F7    血量显示     开 / 关
      F8    详情面板     开 / 关
      F9    效果结算     暂停 / 恢复（关掉后卡片还在，但不生效）
      F10   重新抽词条

  关掉启动器的窗口 = 退出 mod。


----------------------------------------------------------------
  三、第一次用可能遇到的问题
----------------------------------------------------------------

  Q: 提示「没找到游戏主程序」
  A: 把本程序放进游戏文件夹再双击；或者用命令行指定一次路径：
         %s --exe "D:\\你的游戏目录\\PlantsVsZombies.exe"
     指定过一次就会记住。

  Q: 提示「版本校验失败」
  A: 说明你的游戏不是 1.0.0.1051 中文版，或者 exe 被别的 mod 改过。
     按提示里的三条逐一确认。英文原版、年度版、杂交版的偏移都不同，
     不能混用。

  Q: 覆盖层不显示 / 一闪就没了
  A: ① 游戏窗口别最小化——PVZ 一失焦就暂停，覆盖层也会跟着停。
     ② 游戏如果是全屏独占，先按 Alt+Enter 切成窗口模式，否则覆盖层被挡住。

  Q: 提示 OpenProcess 失败
  A: 右键启动器 →「以管理员身份运行」再试。

  Q: 杀毒软件报毒 / 直接把程序删了
  A: 这是误报。本 mod 要读写游戏进程的内存，这个行为特征和木马相似，
     PyInstaller 打包后尤其容易被误判。三个选择：
       ① 给程序加白名单（推荐）
       ② 用「源码版」——它是纯 Python 脚本，可以自己打开看每一行
       ③ 用「源码版」前先看 版本要求.txt 里的说明

  Q: 想反馈问题
  A: %s --diag
     把输出的整段内容发给作者，里面有版本、权限、路径、校验结果。


----------------------------------------------------------------
  四、边界
----------------------------------------------------------------

  · 免费同人作品，不售卖、不内购
  · 不包含游戏本体，不分发原版资源
  · 只在本机内存里改数值，不改你的 exe、不改 main.pak、不动存档文件
  · 关掉程序后，游戏立刻恢复原样

================================================================
"""

    if is_src:
        how = "双击「启动.bat」（需要先装 Python 3.10 以上）"
        exe_hint = "python tools\\launcher.py"
    else:
        how = "双击「%s.exe」" % MOD_NAME
        exe_hint = "%s.exe" % MOD_NAME

    return head % (version, how, exe_hint, exe_hint)


def version_text(version, is_src):
    return """PVZ 海克斯版 v%s（%s）
================================================================

【支持的底包】
  植物大战僵尸 1.0.0.1051 中文版
  PlantsVsZombies.exe  3,007,800 字节
  ImageBase 0x400000，4 个节，无壳

【怎么判断你的版本对不对】
  启动器会自动校验 4 处代码段指纹，全部通过才放行：
    0x44E969  gLawnApp = new LawnApp
    0x407CA9  sizeof(Board) = 0x57000
    0x45F8A9  植物射击倒计时 add [esi+0x58],-1
    0x45F8BF  植物读射击间隔 mov ecx,[esi+0x5c]

  不用 md5 判定，是因为本 mod 自己就会给 exe 打补丁（自动收阳光），
  用 md5 会把「版本对但打过补丁」的玩家全部误杀。

【已验证】
  2026-10-28  1.0.0.1051 中文版 + 自动收阳光补丁 —— 4/4 指纹通过

【未验证】
  · 英文原版 1.0.0.1051（代码段可能一致，但没实测，不敢保证）
  · 年度版 / 杂交版 / 任何改过代码段的版本 —— 不要用

【词条数量】
  33 条（植物 15 + 僵尸 18），全部已实现

【已知限制】
  · 僵尸的「啃食间隔」字段尚未定位，僵尸攻速类词条
    （灵巧 / 狂热者 / 孤勇者 / 屹立不倒）用的是等量增伤，
    数值等价，但不会真的多咬几口。
    植物侧已经改走真实射击间隔（Plant+0x5C），是真的多射豌豆。
  · 僵尸减速倍率字段未定位，冰寒用坐标回推实现额外 30%% 减速。

================================================================
""" % (version, "源码版" if is_src else "exe 版")


def bat_text():
    return """@echo off
chcp 65001 >nul
cd /d "%~dp0"
title PVZ 海克斯版

where python >nul 2>nul
if errorlevel 1 goto nopython

python tools\\launcher.py %*
if errorlevel 1 pause
exit /b 0

:nopython
echo.
echo   [!] 没找到 Python。
echo.
echo   源码版需要 Python 3.10 或以上。
echo   去 https://www.python.org/downloads/ 下载安装，
echo   安装时记得勾选「Add Python to PATH」。
echo.
echo   或者改用 exe 版（不需要 Python）。
echo.
pause
exit /b 1
"""


# ---------------------------------------------------------------- 工具
def ensure_dir(p):
    os.makedirs(p, exist_ok=True)
    return p


def clean(p):
    if os.path.isdir(p):
        shutil.rmtree(p, ignore_errors=True)


# 打包时要排除的临时文件（玩家侧运行时生成，不能带进包里）
EXCLUDE_NAMES = {"launcher.json", "__pycache__", ".DS_Store", "Thumbs.db"}


def zip_dir(src_dir, zip_path, arc_root):
    """把 src_dir 打成一个 zip，zip 内的顶层目录名用 arc_root。"""
    if os.path.isfile(zip_path):
        os.remove(zip_path)
    skipped = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for dirpath, dirnames, filenames in os.walk(src_dir):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_NAMES]
            for fn in filenames:
                if fn in EXCLUDE_NAMES:
                    skipped += 1
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, src_dir)
                z.write(full, os.path.join(arc_root, rel))
    if skipped:
        print("[!] 已排除 %d 个临时文件" % skipped)
    return zip_path


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f TB" % n


# ---------------------------------------------------------------- exe 版
def build_exe(version, out_dir):
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[!] 没装 PyInstaller，跳过 exe 版。")
        print("    安装：.venv\\Scripts\\python.exe -m pip install pyinstaller")
        return None

    clean(DIST_DIR)
    clean(WORK_DIR)
    clean(SPEC_DIR)
    ensure_dir(DIST_DIR)
    ensure_dir(WORK_DIR)
    ensure_dir(SPEC_DIR)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--console",
        "--noupx",                      # 不用 UPX：压缩壳会让杀软更容易误报
        "--name", MOD_NAME,
        "--paths", HERE,
        "--distpath", DIST_DIR,
        "--workpath", WORK_DIR,
        "--specpath", SPEC_DIR,
    ]
    for h in HIDDEN:
        cmd += ["--hidden-import", h]
    cmd.append(os.path.join(HERE, "launcher.py"))

    print("[*] 正在打包 exe（首次会慢一点）……")
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print("[x] PyInstaller 失败，退出码 %d" % r.returncode)
        return None

    exe_src = os.path.join(DIST_DIR, MOD_NAME + ".exe")
    if not os.path.isfile(exe_src):
        print("[x] 没生成 exe：%s" % exe_src)
        return None

    exe_dst = os.path.join(out_dir, MOD_NAME + ".exe")
    shutil.copy2(exe_src, exe_dst)

    # 自测时运行 exe 会在同目录写 launcher.json（记住游戏路径），
    # 那是本机信息，绝不能跟着包发出去
    for junk in ("launcher.json",):
        p = os.path.join(out_dir, junk)
        if os.path.isfile(p):
            os.remove(p)

    print("[OK] exe：%s（%s）" % (exe_dst, human(os.path.getsize(exe_dst))))
    return exe_dst


# ---------------------------------------------------------------- 源码版
def build_src(version, out_dir):
    copied = 0
    for rel in SRC_FILES:
        src = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.isfile(src):
            print("[!] 缺少文件，跳过：%s" % rel)
            continue
        dst = os.path.join(out_dir, rel.replace("/", os.sep))
        ensure_dir(os.path.dirname(dst))
        shutil.copy2(src, dst)
        copied += 1
    print("[OK] 源码版：复制 %d 个文件" % copied)


# ---------------------------------------------------------------- 主流程
def main(argv):
    version = DEFAULT_VERSION
    if "--version" in argv:
        i = argv.index("--version")
        if i + 1 < len(argv):
            version = argv[i + 1]

    no_exe = "--no-exe" in argv

    tag = "v" + version
    exe_pkg = os.path.join(BUILD_ROOT, "%s_%s" % (MOD_NAME, tag))
    src_pkg = os.path.join(BUILD_ROOT, "%s_%s_源码版" % (MOD_NAME, tag))

    print("=" * 60)
    print(" 打包 %s %s" % (MOD_NAME, tag))
    print("=" * 60)
    print()

    clean(exe_pkg)
    clean(src_pkg)
    ensure_dir(exe_pkg)
    ensure_dir(src_pkg)

    # ---- exe 版
    if no_exe:
        print("[*] --no-exe：跳过 exe 版")
    else:
        exe = build_exe(version, exe_pkg)
        if exe:
            with open(os.path.join(exe_pkg, "使用说明.txt"), "w",
                      encoding="utf-8-sig") as f:
                f.write(readme_text(version, False))
            with open(os.path.join(exe_pkg, "版本要求.txt"), "w",
                      encoding="utf-8-sig") as f:
                f.write(version_text(version, False))

    # ---- 源码版
    build_src(version, src_pkg)
    with open(os.path.join(src_pkg, "使用说明.txt"), "w",
              encoding="utf-8-sig") as f:
        f.write(readme_text(version, True))
    with open(os.path.join(src_pkg, "版本要求.txt"), "w",
              encoding="utf-8-sig") as f:
        f.write(version_text(version, True))
    with open(os.path.join(src_pkg, "启动.bat"), "w",
              encoding="utf-8", newline="\r\n") as f:
        f.write(bat_text())

    print()

    # ---- 压缩
    zips = []
    if os.path.isdir(exe_pkg) and os.listdir(exe_pkg):
        zp = exe_pkg + ".zip"
        zip_dir(exe_pkg, zp, os.path.basename(exe_pkg))
        zips.append(zp)
    if os.path.isdir(src_pkg) and os.listdir(src_pkg):
        zp = src_pkg + ".zip"
        zip_dir(src_pkg, zp, os.path.basename(src_pkg))
        zips.append(zp)

    print("=" * 60)
    print(" 完成")
    print("=" * 60)
    for d in (exe_pkg, src_pkg):
        if os.path.isdir(d) and os.listdir(d):
            print("  目录  %s" % d)
    for z in zips:
        print("  压缩  %s（%s）" % (z, human(os.path.getsize(z))))
    print()
    print("  发给别人：把 zip 传过去，对方解压、双击即可。")
    print("  提醒对方：本包不含游戏本体，需要自备 1.0.0.1051 中文版。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
