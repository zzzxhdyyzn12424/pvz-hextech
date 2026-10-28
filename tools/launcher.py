# -*- coding: utf-8 -*-
"""
launcher.py —— 给「别人」用的一键启动器

玩家不需要知道 Python、偏移量、内存读写。双击就是这套流程：

    1. 找游戏（运行中的进程 → 上次记住的路径 → exe 同目录 → 让玩家选）
    2. 校验版本（4 处代码段指纹，见 version_check.py）
    3. 校验通过 → 拉起游戏内覆盖层
    4. 校验失败 → 一句人话说明该怎么办，并把诊断信息写进文件

命令：

    python tools/launcher.py              启动（默认）
    python tools/launcher.py --check      只校验版本，不启动覆盖层
    python tools/launcher.py --diag       打印诊断信息（玩家反馈问题时用）
    python tools/launcher.py --exe PATH   记住游戏 exe 路径
    python tools/launcher.py --no-wait    出错时不等待按键（脚本调用用）

配置文件 `launcher.json` 放在程序所在目录，只存游戏路径，不联网。
"""

import json
import os
import subprocess
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import version_check as V


def app_dir():
    """打包后是 exe 所在目录；源码运行时是项目根目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(HERE)


CONF_PATH = os.path.join(app_dir(), "launcher.json")


def setup_console():
    """把控制台切到 UTF-8。

    源码版靠 bat 里的 chcp 65001，打包版没有 bat，
    不设的话中文在 UTF-8 终端里会变乱码。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:                       # noqa: BLE001
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                   # noqa: BLE001
            pass


# ---------------------------------------------------------------- 配置
def load_conf():
    try:
        with open(CONF_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:                       # noqa: BLE001
        return {}


def save_conf(conf):
    try:
        with open(CONF_PATH, "w", encoding="utf-8") as f:
            json.dump(conf, f, ensure_ascii=False, indent=2)
        return True
    except Exception:                       # noqa: BLE001
        return False


# ---------------------------------------------------------------- 找游戏
def guess_exe(conf):
    """按优先级返回候选 exe 路径列表（只返回真实存在的）。"""
    cands = []

    # 1) 配置里记住的
    p = conf.get("exe")
    if p:
        cands.append(p)

    # 2) 运行中进程的真实映像路径
    pid = V.find_pid()
    if pid:
        p = V.process_exe_path(pid)
        if p:
            cands.append(p)

    # 3) 程序同目录（很多玩家把 mod 丢进游戏文件夹）
    cands.append(os.path.join(app_dir(), V.EXE_NAME))
    # 4) 上一级目录
    cands.append(os.path.join(os.path.dirname(app_dir()), V.EXE_NAME))

    out = []
    seen = set()
    for c in cands:
        if not c:
            continue
        c = os.path.abspath(c)
        k = c.lower()
        if k in seen:
            continue
        seen.add(k)
        if os.path.isfile(c):
            out.append(c)
    return out


def ask_exe():
    """弹文件选择框让玩家指定游戏 exe。"""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(
            title="请选择 PlantsVsZombies.exe",
            filetypes=[("PVZ 主程序", "PlantsVsZombies.exe"),
                       ("可执行文件", "*.exe"), ("所有文件", "*.*")])
        root.destroy()
        return path or None
    except Exception:                       # noqa: BLE001
        return None


# ---------------------------------------------------------------- 输出
LINE = "=" * 60


def banner():
    print(LINE)
    print("  PVZ 海克斯版 · 启动器")
    print(LINE)
    print()


def wait_exit(no_wait):
    if no_wait:
        return
    try:
        input("\n按回车键关闭……")
    except (EOFError, KeyboardInterrupt):
        pass


def fail(title, detail_lines, no_wait=False):
    print("[x] " + title)
    print()
    for ln in detail_lines:
        print("    " + ln)
    print()
    wait_exit(no_wait)
    return 2


# ---------------------------------------------------------------- 主流程
def run_check(exe_hint=None, interactive=True):
    """返回 (是否通过, Result, exe路径)。"""
    conf = load_conf()

    # 优先：游戏正在运行 → 直接读进程内存校验（最准，且不受文件被锁影响）
    pid = V.find_pid()
    if pid:
        print("[1/3] 检测到游戏正在运行 (pid=%d)" % pid)
        res = V.check_process(pid)
        path = res.exe_path or exe_hint
        if path and not conf.get("exe"):
            conf["exe"] = path
            save_conf(conf)
        return res.ok, res, path

    print("[1/3] 游戏没在运行，改为校验 exe 文件")

    cands = guess_exe(conf)
    if exe_hint:
        cands.insert(0, exe_hint)

    path = cands[0] if cands else None
    if path:
        print("      找到：%s" % path)
    elif interactive:
        print("      没自动找到，请手动选择游戏主程序……")
        path = ask_exe()
        if not path:
            return False, None, None
        conf["exe"] = path
        save_conf(conf)
    else:
        return False, None, None

    res = V.check_file(path)
    if res.ok:
        conf["exe"] = os.path.abspath(path)
        save_conf(conf)
    return res.ok, res, path


def cmd_launch(exe_hint=None, no_wait=False, check_only=False):
    banner()

    ok, res, path = run_check(exe_hint)

    if res is None:
        return fail("没找到游戏主程序。", [
            "请把本程序放在游戏文件夹里，或者手动指定：",
            "    launcher.exe --exe \"D:\\你的游戏\\PlantsVsZombies.exe\"",
            "",
            "游戏主程序文件名必须是 PlantsVsZombies.exe。",
        ], no_wait)

    print()
    print("[2/3] 版本校验")
    print(V.format_report(res, verbose=True))
    print()

    if not ok:
        return fail("版本不对，为安全起见不启动。", [
            "本 mod 是通过直接读写游戏内存实现的，偏移量必须与游戏版本严格对应。",
            "版本不对时强行运行会改错内存，可能让游戏崩溃或存档损坏。",
            "",
            "请使用：植物大战僵尸 1.0.0.1051 中文版",
            "  · 右键 PlantsVsZombies.exe → 属性 → 详细信息，看文件版本",
            "  · 文件大小应约为 3,007,800 字节",
        ], no_wait)

    if check_only:
        print("[3/3] 仅校验模式，不启动覆盖层。")
        wait_exit(no_wait)
        return 0

    # 覆盖层要挂在游戏窗口上，游戏没跑就没得挂
    if not V.find_pid():
        return fail("游戏还没启动。", [
            "版本没问题，但需要先把游戏打开。",
            "",
            "  1. 启动 PlantsVsZombies.exe",
            "  2. 随便进一关，让场上出现植物和僵尸",
            "  3. 再双击本程序",
            "",
            "（PVZ 一旦失焦就会暂停，所以 mod 必须挂在运行中的游戏上。）",
        ], no_wait)

    print("[3/3] 启动游戏内覆盖层")
    print()
    print("  F7   血量显示   开/关")
    print("  F8   详情面板   开/关")
    print("  F9   效果结算   暂停/恢复")
    print("  F10  重新抽词条")
    print()
    print("  ⚠ 游戏窗口请保持「非最小化」。PVZ 一旦失焦就会暂停，覆盖层也会跟着停。")
    print("  ⚠ 游戏若是全屏独占，请先按 Alt+Enter 切成窗口模式，否则覆盖层会被挡住。")
    print()
    print("  关掉这个窗口 = 退出 mod。")
    print()

    return start_overlay(no_wait)


def start_overlay(no_wait=False):
    """拉起 hextech.py 的 run 模式。"""
    if getattr(sys, "frozen", False):
        # 打包版：直接调用内置的 hextech 模块
        try:
            import hextech
            sys.argv = [sys.argv[0], "run"]
            return hextech.main()
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            if code:
                print("[x] 覆盖层异常退出（code=%s）" % code)
                wait_exit(no_wait)
            return code
        except Exception:                   # noqa: BLE001
            print("[x] 覆盖层启动失败：")
            traceback.print_exc()
            wait_exit(no_wait)
            return 3
    else:
        py = sys.executable
        cmd = [py, os.path.join(HERE, "hextech.py"), "run"]
        try:
            return subprocess.call(cmd)
        except KeyboardInterrupt:
            return 0


# ---------------------------------------------------------------- 诊断
def cmd_diag():
    banner()
    print("诊断信息（反馈问题时请整段复制）")
    print(LINE)
    print("Python        : %s" % sys.version.replace("\n", " "))
    print("打包模式      : %s" % ("是" if getattr(sys, "frozen", False) else "否（源码运行）"))
    print("程序目录      : %s" % app_dir())
    print("配置文件      : %s%s" % (CONF_PATH,
                                  "" if os.path.isfile(CONF_PATH) else "（不存在）"))
    print("管理员权限    : %s" % ("是" if _is_admin() else "否"))
    print("系统          : %s" % (sys.getwindowsversion(),))
    print()

    pid = V.find_pid()
    print("游戏进程      : %s" % (("运行中 pid=%d" % pid) if pid else "未运行"))
    if pid:
        print("进程映像      : %s" % (V.process_exe_path(pid) or "读取失败"))
    print()

    conf = load_conf()
    print("记住的路径    : %s" % conf.get("exe", "（无）"))
    cands = guess_exe(conf)
    print("候选路径      : %d 个" % len(cands))
    for c in cands:
        print("  - %s" % c)
    print()

    target = cands[0] if cands else None
    if target:
        res = V.check_file(target)
        print("版本校验      : %s" % res.summary())
        print(V.format_report(res, verbose=True))
    else:
        print("版本校验      : 跳过（没找到 exe）")

    print()
    print(LINE)
    print("把上面这段发给作者即可。")
    return 0


def _is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:                       # noqa: BLE001
        return False


# ---------------------------------------------------------------- 入口
def main(argv):
    setup_console()
    no_wait = "--no-wait" in argv

    if "--diag" in argv:
        rc = cmd_diag()
        wait_exit(no_wait)
        return rc

    exe_hint = None
    if "--exe" in argv:
        i = argv.index("--exe")
        if i + 1 < len(argv):
            exe_hint = argv[i + 1]
            conf = load_conf()
            conf["exe"] = os.path.abspath(exe_hint)
            save_conf(conf)
            print("已记住游戏路径：%s" % conf["exe"])
            print()

    check_only = "--check" in argv

    try:
        return cmd_launch(exe_hint, no_wait, check_only)
    except KeyboardInterrupt:
        print("\n已退出。")
        return 0
    except Exception:                       # noqa: BLE001
        print("\n[x] 启动器自身出错，请把下面的信息发给作者：")
        print(LINE)
        traceback.print_exc()
        print(LINE)
        wait_exit(no_wait)
        return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
