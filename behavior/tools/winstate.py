#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""winstate.py —— 看游戏窗口到底是不是在前台（判断游戏有没有因为失焦而暂停）"""
import ctypes
import ctypes.wintypes as wt

u = ctypes.WinDLL("user32", use_last_error=True)
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
u.IsIconic.argtypes = [wt.HWND]
u.IsWindowVisible.argtypes = [wt.HWND]
u.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
u.FindWindowW.restype = wt.HWND
u.GetForegroundWindow.restype = wt.HWND


def title(h):
    b = ctypes.create_unicode_buffer(512)
    u.GetWindowTextW(h, b, 512)
    return b.value


def pid_of(h):
    p = wt.DWORD()
    u.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value


fg = u.GetForegroundWindow()
print("前台窗口 : %r  (PID=%d)" % (title(fg), pid_of(fg)))

h = u.FindWindowW(None, "植物大战僵尸")
if not h:
    # 中文版标题可能不同，退而求其次按进程找
    print("按标题 '植物大战僵尸' 没找到窗口，下面列出所有可见窗口标题：")
    hits = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, lp):
        if u.IsWindowVisible(hwnd):
            t = title(hwnd)
            if t:
                hits.append((hwnd, t))
        return True

    u.EnumWindows(cb, 0)
    for hwnd, t in hits:
        print("   HWND=%s  %r" % (hwnd, t))
else:
    print("游戏窗口 : HWND=%s  标题=%r" % (h, title(h)))
    print("最小化   : %s" % bool(u.IsIconic(h)))
    print("可见     : %s" % bool(u.IsWindowVisible(h)))
    print("在前台   : %s" % (h == fg))
