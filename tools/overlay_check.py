#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
overlay_check.py —— 隔离诊断：透明覆盖窗口到底有没有画出像素

不碰游戏，纯 Tk + Win32。跑完在 tools/_diag/ 下留三张图：
    win_printwindow.png   窗口自绘内容（PrintWindow PW_RENDERFULLCONTENT）
    win_bitblt.png        窗口 DC 直抓
    screen.png            屏幕该区域实拍（含分层窗口）

用法：
    python tools/overlay_check.py            # 默认用 colorkey 透明
    python tools/overlay_check.py opaque     # 完全不透明，作对照
"""

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

import tkinter as tk
from PIL import Image

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
GA_ROOT = 2
LWA_COLORKEY = 0x00000001
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
PW_RENDERFULLCONTENT = 0x00000002

user32.GetAncestor.argtypes = [wt.HWND, ctypes.c_uint]
user32.GetAncestor.restype = wt.HWND
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.COLORREF,
                                              ctypes.c_ubyte, wt.DWORD]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowDC.argtypes = [wt.HWND]
user32.GetWindowDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, ctypes.c_uint]

gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
                            ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wt.UINT]
gdi32.GetDIBits.restype = ctypes.c_int


def grab_window(hwnd, w, h, method):
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if method == "print":
            ok = user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)
        else:
            ok = gdi32.BitBlt(mem, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        lines = gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
        if lines != h:
            return None, ok
        img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
        return img.convert("RGB"), ok
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "colorkey"
    key = "#010203"
    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_diag")
    os.makedirs(outdir, exist_ok=True)

    W, H, PX, PY = 640, 400, 120, 120

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    if mode == "colorkey":
        root.attributes("-transparentcolor", key)
    root.configure(bg=key if mode == "colorkey" else "#101010")
    root.geometry("%dx%d+%d+%d" % (W, H, PX, PY))
    cv = tk.Canvas(root, bg=key if mode == "colorkey" else "#101010",
                   highlightthickness=0, bd=0)
    cv.pack(fill="both", expand=True)
    cv.create_rectangle(30, 30, 230, 130, fill="#E24A4A", outline="#FFFFFF", width=3)
    cv.create_text(130, 80, text="HEXTECH", fill="#FFFFFF",
                   font=("Microsoft YaHei", 20, "bold"))
    cv.create_rectangle(280, 30, 330, 80, fill="#C8D2DC", outline="#10161C")
    cv.create_text(305, 55, text="攻", fill="#0B0E12",
                   font=("Microsoft YaHei", 10, "bold"))
    root.update()

    hwnd = user32.GetAncestor(root.winfo_id(), GA_ROOT) or root.winfo_id()
    ex_before = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)

    ex = ex_before | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    old = user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
    err1 = ctypes.get_last_error()
    # ★ 关键：改 exstyle 必须 SetWindowPos 冲刷缓存，否则新标志不生效
    ok_sp = user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                                | SWP_NOACTIVATE | SWP_FRAMECHANGED)
    err2 = ctypes.get_last_error()
    # ★ 关键：LAYERED 设好后显式补一次色键（tk 的 -transparentcolor 可能已被重置）
    ck = 0x00030201                        # COLORREF = BGR
    ok_lwa = user32.SetLayeredWindowAttributes(hwnd, ck, 0, LWA_COLORKEY)
    err3 = ctypes.get_last_error()

    ex_after = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    root.update()
    time.sleep(0.35)

    print("hwnd        = %d" % hwnd)
    print("mode        = %s" % mode)
    print("exstyle 前  = 0x%08X" % (ex_before & 0xFFFFFFFF))
    print("exstyle 后  = 0x%08X" % (ex_after & 0xFFFFFFFF))
    print("  LAYERED     %s" % bool(ex_after & WS_EX_LAYERED))
    print("  TRANSPARENT %s" % bool(ex_after & WS_EX_TRANSPARENT))
    print("  TOOLWINDOW  %s" % bool(ex_after & WS_EX_TOOLWINDOW))
    print("  NOACTIVATE  %s" % bool(ex_after & WS_EX_NOACTIVATE))
    print("SetWindowLongW 返回 0x%08X err=%d" % (old & 0xFFFFFFFF, err1))
    print("SetWindowPos   = %s err=%d" % (bool(ok_sp), err2))
    print("SetLayeredWindowAttributes = %s err=%d" % (bool(ok_lwa), err3))
    print("IsWindowVisible = %s" % bool(user32.IsWindowVisible(hwnd)))
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    print("window rect = (%d,%d,%d,%d)" % (r.left, r.top, r.right, r.bottom))

    for method, fn in (("print", "win_printwindow.png"), ("bitblt", "win_bitblt.png")):
        img, ok = grab_window(hwnd, W, H, method)
        if img is None:
            print("[%s] 抓取失败 ok=%s" % (method, ok))
            continue
        p = os.path.join(outdir, fn)
        img.save(p)
        colors = img.convert("RGB").getcolors(1 << 20) or []
        colors.sort(reverse=True)
        print("[%s] ok=%s -> %s" % (method, ok, p))
        print("        前3主色 %s" % [c for c in colors[:3]])

    from PIL import ImageGrab
    sp = os.path.join(outdir, "screen.png")
    ImageGrab.grab(bbox=(PX, PY, PX + W, PY + H),
                   include_layered_windows=True, all_screens=True).save(sp)
    print("[screen] -> %s" % sp)

    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
