#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hextech.py —— 植物大战僵尸「海克斯版」入口

核心逻辑在 hextech_core.py，这里只做两件事：
    1. 命令行（探针 / 校准）
    2. 透明覆盖窗口（头顶海克斯卡片 + F8 详情 + F9 开关）

用法：
    python tools/hextech.py panel            # ★ 打开海克斯面板（点按钮看词条）
    python tools/hextech.py panel --auto     # 打开并每秒自动刷新
    python tools/hextech.py probe            # 命令行打印当前局面（只读）
    python tools/hextech.py probe 30         # 连打 30 秒
    python tools/hextech.py run              # 启动覆盖窗口 + 效果引擎
    python tools/hextech.py run --dry        # 只显示卡片，不结算效果
    python tools/hextech.py run --dry --shot out.png --after 2.5
                                             # 跑 2.5 秒抓覆盖层自身内容后退出（自检用）
    python tools/hextech.py run --geom 800x600+60+60
                                             # 游戏最小化时也能离线校验布局
    python tools/hextech.py live 10          # 实时采样 10 秒，看哪些字段在动
    python tools/hextech.py calibrate        # 校准数组步长 / dump 字段
    python tools/hextech.py watchdiff zombie 15
    python tools/hextech.py armortest 1 500  # 给所有僵尸挂原生护盾（挑贴图用）
    python tools/hextech.py armortest 0      # 清掉

热键（在 run 模式下）：
    F7   血量显示 开 / 关（植物数字、僵尸三色血条）
    F8   显示 / 隐藏详情面板
    F9   开 / 关 效果结算（卡片仍然显示）
    F10  重新给全场对象抽一次词条
"""

import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hextech_core as C

user32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = C.k32
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.IsIconic.argtypes = [wt.HWND]
user32.GetAncestor.argtypes = [wt.HWND, ctypes.c_uint]
user32.GetAncestor.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
user32.IsWindowVisible.argtypes = [wt.HWND]

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
GA_ROOT = 2

VK_F7, VK_F8, VK_F9, VK_F10 = 0x76, 0x77, 0x78, 0x79

TRANSPARENT_KEY = "#010203"

# 血量配色：照抄杂交版的分层语义（蓝=护盾/防具，黄=头盔，红=本体）
HP_BAR_W = 46
HP_BAR_H = 3
HP_BAR_GAP = 1
C_SHIELD = "#4A90E2"
C_HELMET = "#F0B429"
C_BODY = "#E24A4A"
C_BAR_BG = "#14191F"
C_HP_TEXT = "#E8EEF5"
C_HP_MID = "#F0B429"
C_HP_LOW = "#FF5A5A"

# 游戏画面在客户端里的偏移（PVZ 是 800x600 固定画面，一般不用改）
OVERLAY_DX = 0
OVERLAY_DY = 0


# ============================================================ 窗口定位
def find_game_hwnd(pid):
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        p = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid:
            r = wt.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(r))
            if r.right - r.left > 400 and r.bottom - r.top > 300:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def client_origin(hwnd):
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def client_size(hwnd):
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


LWA_COLORKEY = 0x00000001
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER = 0x0001, 0x0002, 0x0004
SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x0010, 0x0020
PW_RENDERFULLCONTENT = 0x00000002

user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.COLORREF,
                                              ctypes.c_ubyte, wt.DWORD]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowDC.argtypes = [wt.HWND]
user32.GetWindowDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, ctypes.c_uint]

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]


class _BMIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class _BMI(ctypes.Structure):
    _fields_ = [("bmiHeader", _BMIH), ("bmiColors", wt.DWORD * 3)]


gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
                            ctypes.c_void_p, ctypes.POINTER(_BMI), wt.UINT]
gdi32.GetDIBits.restype = ctypes.c_int


def make_clickthrough(root, key=None):
    """把 Tk 窗口改成「置顶 + 点击穿透 + 色键透明」。

    ★ 两个坑，缺一个就白干：
      1. 改完 exstyle 必须 SetWindowPos(SWP_FRAMECHANGED) 冲刷窗口缓存，否则标志不生效；
      2. tk 的 -transparentcolor 会先设一次 LWA，之后 SetWindowLongW 可能把它冲掉，
         所以最后再显式补一次 SetLayeredWindowAttributes。
    """
    hwnd = user32.GetAncestor(root.winfo_id(), GA_ROOT) or root.winfo_id()
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ex |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                        | SWP_NOACTIVATE | SWP_FRAMECHANGED)
    if key:
        r = int(key[1:3], 16)
        g = int(key[3:5], 16)
        b = int(key[5:7], 16)
        user32.SetLayeredWindowAttributes(hwnd, r | (g << 8) | (b << 16),
                                          0, LWA_COLORKEY)
    return hwnd


def grab_window_png(hwnd, path, pad=0):
    """用 PrintWindow 抓窗口自身内容存 PNG（能抓到分层窗口的真实绘制）。"""
    from PIL import Image
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return False, (0, 0)
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        # ★ 必须用 BitBlt 而不是 PrintWindow：
        #   PrintWindow 会把色键区域渲染成纯黑，分不清「透明」和「黑色文字」。
        gdi32.BitBlt(mem, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)
        bi = _BMI()
        bi.bmiHeader.biSize = ctypes.sizeof(_BMIH)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        if gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0) != h:
            return False, (w, h)
        Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1) \
             .convert("RGB").save(path)
        return True, (w, h)
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)


# ============================================================ 抢焦点
k32.GetCurrentThreadId.restype = wt.DWORD
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetFocus.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [wt.HWND]

SW_RESTORE = 9


def focus_game(pid):
    """把游戏窗口拉到前台。PVZ 失焦会自动暂停，采样前必须先抢回焦点。"""
    hwnd = find_game_hwnd(pid)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, SW_RESTORE)
    fg = user32.GetForegroundWindow()
    t_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    t_me = k32.GetCurrentThreadId()
    ok = False
    if t_fg and t_fg != t_me:
        if user32.AttachThreadInput(t_me, t_fg, True):
            ok = bool(user32.SetForegroundWindow(hwnd))
            user32.SetFocus(hwnd)
            user32.AttachThreadInput(t_me, t_fg, False)
    else:
        ok = bool(user32.SetForegroundWindow(hwnd))
    user32.BringWindowToTop(hwnd)
    return ok or bool(hwnd)


# ============================================================ 覆盖窗口
class Overlay:
    CARD_H = 17
    FONT = ("Microsoft YaHei", 9, "bold")
    FONT_SM = ("Microsoft YaHei", 8)
    FONT_PANEL = ("Microsoft YaHei", 9)

    def __init__(self, engine, game):
        import tkinter as tk
        self.tk = tk
        self.eng = engine
        self.g = game
        self.show_panel = False
        self.show_hp = True
        self.dry = False
        self.geom_override = None
        self._last_hwnd = None
        self._last_geo = None

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
        self.root.configure(bg=TRANSPARENT_KEY)
        self.canvas = tk.Canvas(self.root, bg=TRANSPARENT_KEY,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.hwnd = make_clickthrough(self.root, TRANSPARENT_KEY)
        self._keys = {VK_F7: False, VK_F8: False, VK_F9: False, VK_F10: False}
        self._fps_t = time.time()
        self._fps_n = 0
        self.fps = 0.0

    # ---------- 键 ----------
    def _poll_keys(self):
        for vk in self._keys:
            down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
            if down and not self._keys[vk]:
                if vk == VK_F7:
                    self.show_hp = not self.show_hp
                elif vk == VK_F8:
                    self.show_panel = not self.show_panel
                elif vk == VK_F9:
                    self.eng.enabled = not self.eng.enabled
                elif vk == VK_F10:
                    self.eng.plant_state.clear()
                    self.eng.zombie_state.clear()
            self._keys[vk] = down

    # ---------- 定位 ----------
    def _sync_window(self):
        if self.geom_override:                   # 离线校验用：不依赖游戏窗口
            geo = self.geom_override
            if geo != self._last_geo:
                self.root.geometry(geo)
                self._last_geo = geo
            if self._last_hwnd is None:
                self._last_hwnd = -1
                self.root.deiconify()
                make_clickthrough(self.root, TRANSPARENT_KEY)
                self.root.attributes("-topmost", True)
            w, h = geo.split("+")[0].split("x")
            return int(w), int(h)

        hwnd = find_game_hwnd(self.g.pid)
        if not hwnd:
            if self._last_hwnd is not None:      # 只在「刚丢失」时收一次，别每帧折腾
                self.root.withdraw()
                self._last_hwnd = None
            return None
        # 最小化时客户区是 0x0，此时不画（免得卡片飘在桌面上），但记住几何
        if user32.IsIconic(hwnd):
            if self._last_hwnd is not None:
                self.root.withdraw()
                self._last_hwnd = None
            return None
        ox, oy = client_origin(hwnd)
        w, h = client_size(hwnd)
        geo = "%dx%d+%d+%d" % (w, h, ox, oy)
        if geo != self._last_geo:
            self.root.geometry(geo)
            self._last_geo = geo
        if self._last_hwnd != hwnd:
            self._last_hwnd = hwnd
            self.root.deiconify()
            # 重新 map 过之后分层属性可能被重置，补一次
            make_clickthrough(self.root, TRANSPARENT_KEY)
            self.root.attributes("-topmost", True)
        return w, h

    # ---------- 血量（参考杂交版：僵尸血条分层，植物数字） ----------
    def _hp_color(self, cur, mx):
        r = (cur / mx) if mx else 0
        if r <= 0.25:
            return C_HP_LOW
        if r <= 0.5:
            return C_HP_MID
        return C_HP_TEXT

    def _draw_hp(self, c):
        for o in self.eng.objects:
            x, y = o["x"] + OVERLAY_DX, o["y"] + OVERLAY_DY
            if x < -80 or x > 900 or y < -40 or y > 660:
                continue
            st = o.get("state")
            if o["kind"] == "plant":
                hp = o["hp"]
                mx = o.get("hp_max") or (st.hp_max if st else hp) or 1
                c.create_text(x + (o.get("w") or 80) / 2.0, y - 8,
                              text=str(int(hp)), anchor="s",
                              fill=self._hp_color(hp, mx),
                              font=("Microsoft YaHei", 8, "bold"))
                continue

            # 僵尸：蓝(盾) / 黄(头盔) / 红(本体) 三层，从上往下叠
            mx_body = (st.hp_max if st else 0) or o["hp"] or 1
            layers = []
            if o.get("shield_max", 0) > 0:
                layers.append((o["shield"], o["shield_max"], C_SHIELD))
            if o.get("helmet_max", 0) > 0:
                layers.append((o["helmet"], o["helmet_max"], C_HELMET))
            layers.append((o["hp"], mx_body, C_BODY))

            n = len(layers)
            block_h = n * HP_BAR_H + (n - 1) * HP_BAR_GAP
            cx = x + (o.get("w") or 60) / 2.0
            x0 = cx - HP_BAR_W / 2.0
            top = y - 8 - block_h
            for i, (cur, mx, col) in enumerate(layers):
                yy = top + i * (HP_BAR_H + HP_BAR_GAP)
                c.create_rectangle(x0, yy, x0 + HP_BAR_W, yy + HP_BAR_H,
                                   fill=C_BAR_BG, outline="")
                if mx > 0 and cur > 0:
                    fw = HP_BAR_W * min(1.0, cur / float(mx))
                    c.create_rectangle(x0, yy, x0 + fw, yy + HP_BAR_H,
                                       fill=col, outline="")
        return True

    # ---------- 卡片 ----------
    def _draw_cards(self, c):
        drawn = 0
        for o in self.eng.objects:
            hexes = o.get("hexes") or []
            if not hexes:
                continue
            w = o.get("w") or 80
            x = o["x"] + OVERLAY_DX + w / 2.0
            # 血条占 y-8 往上约 15px，卡片再往上让开
            y = o["y"] + OVERLAY_DY - (38 if self.show_hp else 20)
            if x < -60 or x > 900 or y < -40 or y > 640:
                continue
            n = len(hexes)
            step = self.CARD_H + 3
            total_w = n * step - 3
            x0 = x - total_w / 2.0
            for i, hid in enumerate(hexes):
                d = C.HEX_BY_ID.get(hid)
                if not d:
                    continue
                col = C.TIER_COLOR.get(d["tier"], "#888888")
                cx = x0 + i * step
                c.create_rectangle(cx, y, cx + self.CARD_H, y + self.CARD_H,
                                   fill=col, outline="#10161C", width=1)
                c.create_text(cx + self.CARD_H / 2, y + self.CARD_H / 2,
                              text=d["short"], fill="#0B0E12", font=self.FONT_SM)
                drawn += 1
        return drawn

    # ---------- 详情面板 ----------
    def _draw_panel(self, c, w, h):
        pw, ph = 520, min(h - 40, 560)
        px, py = w - pw - 14, 14
        c.create_rectangle(px, py, px + pw, py + ph,
                           fill="#0E1216", outline="#C8D2DC", width=1)
        lines = []
        st = self.eng.stats
        lines.append("海克斯版   关卡 %s   阳光 %s"
                     % (self.g.level(), self.g.sun()))
        lines.append("随机 %d~%d 条 · 植物 %d 株 · 僵尸 %d 个 · %s"
                     % (C.HEX_MIN, C.HEX_MAX,
                        st["plant"], st["zombie"],
                        "结算中" if self.eng.enabled else "已暂停"))
        lines.append("-" * 46)

        plants = [o for o in self.eng.objects if o["kind"] == "plant"]
        zombies = [o for o in self.eng.objects if o["kind"] == "zombie"]
        if plants:
            lines.append("【植物】")
            for p in plants:
                nm = C.PLANT_NAMES.get(p["type"], "?%d" % p["type"])
                hx = "/".join(C.HEX_BY_ID[h]["name"] for h in p["hexes"])
                lines.append("  %s(%d,%d)  %s" % (nm, p["row"], p["col"], hx))
        if zombies:
            lines.append("【僵尸】")
            for z in zombies[:24]:
                nm = C.ZOMBIE_NAMES.get(z["type"], "?%d" % z["type"])
                hx = "/".join(C.HEX_BY_ID[h]["name"] for h in z["hexes"])
                lines.append("  %s  %d/%d  %s"
                             % (nm, z["hp"], z["state"].hp_max, hx))
            if len(zombies) > 24:
                lines.append("  …还有 %d 个" % (len(zombies) - 24))
        lines.append("-" * 46)
        lines.append("F8 关闭面板   F9 暂停/恢复结算   F10 重抽")
        for i, s in enumerate(lines[:40]):
            c.create_text(px + 10, py + 10 + i * 13, text=s, anchor="nw",
                          fill="#DCE4EC", font=self.FONT_PANEL)
        return len(lines)

    # ---------- 盲盒卡 ----------
    def _draw_blindbox(self, c):
        """在盲盒卡上盖一个「?」。卡面是游戏画的，我们只贴个角标。"""
        box = getattr(self.eng, "blindbox", None)
        if not box:
            return 0
        x = box["x"] + OVERLAY_DX
        y = box["y"] + OVERLAY_DY
        if x < -40 or x > 900 or y < -40 or y > 640:
            return 0
        s = 17
        c.create_rectangle(x, y, x + s, y + s,
                           fill="#B06CF0", outline="#0B0E12", width=1)
        c.create_text(x + s / 2.0, y + s / 2.0, text="?",
                      fill="#0B0E12", font=self.FONT_SM)
        # 冷却条：知道它什么时候能用
        ref = box.get("refresh") or 0
        if ref > 0:
            total = self.g.i32(C.GPLANTDEFS + box["type"] * C.PLANTDEF_STRIDE
                               + C.PLANTDEF_REFRESH) or 0
            if total > 0:
                done = max(0.0, min(1.0, 1.0 - float(ref) / total))
                c.create_rectangle(x, y + s + 1, x + 44, y + s + 4,
                                   fill="#2A3038", outline="")
                if done > 0:
                    c.create_rectangle(x, y + s + 1, x + 44 * done,
                                       y + s + 4, fill="#B06CF0", outline="")
        return 1

    # ---------- 主循环 ----------
    def _loop(self):
        self._poll_keys()
        try:
            self.eng.tick()
        except Exception as e:      # 游戏切场景 / 内存失效时不要崩
            self.canvas.delete("all")
            self.canvas.create_text(20, 20, anchor="nw",
                                    text="引擎异常：%s" % e, fill="#FF6060")
            self.root.after(300, self._loop)
            return

        self._fps_n += 1
        if time.time() - self._fps_t >= 1.0:
            self.fps = self._fps_n / (time.time() - self._fps_t)
            self._fps_n = 0
            self._fps_t = time.time()

        geo = self._sync_window()
        self.canvas.delete("all")
        if geo:
            w, h = geo
            if self.show_hp:
                self._draw_hp(self.canvas)
            n = self._draw_cards(self.canvas)
            b = self._draw_blindbox(self.canvas)
            if self.show_panel:
                self._draw_panel(self.canvas, w, h)
            else:
                self.canvas.create_text(
                    12, h - 20, anchor="w",
                    text="海克斯版  %.0ffps  卡片 %d  盲盒 %s  F7 血量%s  F8 详情  F9 %s"
                         % (self.fps, n,
                            ("第%d格" % (self.eng.blindbox["slot"] + 1)) if b else "关",
                            "开" if self.show_hp else "关",
                            "结算中" if self.eng.enabled else "已暂停"),
                    fill="#7A8896", font=self.FONT_SM)
        self.root.after(50, self._loop)

    def run(self, shot=None, after=2.0):
        if shot:
            def _do_shot():
                try:
                    self.eng.tick()
                    geo = self._sync_window()
                    self.canvas.delete("all")
                    if self.show_hp:
                        self._draw_hp(self.canvas)
                    n = self._draw_cards(self.canvas)
                    if geo:
                        self.canvas.create_text(
                            12, geo[1] - 20, anchor="w",
                            text="海克斯版  卡片 %d  F7 血量  F8 详情" % n,
                            fill="#7A8896", font=self.FONT_SM)
                    self.root.update()
                    ok, size = grab_window_png(self.hwnd, shot)
                    print("[shot] %s  %s  卡片 %d  对象 %d  尺寸 %s"
                          % (os.path.basename(shot), "OK" if ok else "FAIL",
                             n, len(self.eng.objects), size))
                except Exception as e:
                    print("[shot] 异常：%r" % (e,))
                finally:
                    self.root.destroy()
            self.root.after(int(after * 1000), _do_shot)
        self.root.after(10, self._loop)
        self.root.mainloop()


# ============================================================ 校准（原开发工具）
def _hexdump(raw, base=0, step=4, cols=4):
    out = []
    for off in range(0, len(raw), step):
        chunk = raw[off:off + step]
        if len(chunk) < step:
            break
        v = struct.unpack("<I", chunk)[0]
        f = struct.unpack("<f", chunk)[0]
        out.append("+0x%03X %08X %12d %14.3f"
                   % (base + off, v, struct.unpack("<i", chunk)[0], f))
    lines = []
    for i in range(0, len(out), cols):
        lines.append("   ".join(out[i:i + cols]))
    return "\n".join(lines)


def cmd_calibrate(g):
    print("PID %d   gApp 0x%08X   Board 0x%08X"
          % (g.pid, g.gapp or 0, g.board or 0))
    if not g.board:
        print("Board 为空 —— 先进入一个关卡。")
        return 1
    print("关卡 %s   场景 %s   阳光 %s"
          % (g.level(), g.i32(g.board + C.B_SCENE), g.sun()))

    for label, kind, stride, dead in (
            ("植物", "plant", C.PLANT_STRIDE, C.P_DEAD),
            ("僵尸", "zombie", C.ZOMBIE_STRIDE, C.Z_DEAD)):
        objs = g.scan_plants() if kind == "plant" else g.scan_zombies()
        print("\n" + "=" * 70)
        print("【%s】步长 0x%X，扫到 %d 个存活对象" % (label, stride, len(objs)))
        if not objs:
            continue
        a = objs[0]["addr"]
        raw = g.read(a, 0x100)
        if raw:
            print("--- 第一个对象 0x%08X（+0x00..+0x100）---" % a)
            print(_hexdump(raw))
        print("dead(+0x%X) = %s" % (dead, g.u8(a + dead)))
    return 0


def cmd_watchdiff(g, kind="zombie", seconds=15.0):
    import collections
    objs = g.scan_plants() if kind == "plant" else g.scan_zombies()
    if not objs:
        print("没有对象")
        return 1
    target = objs[0]["addr"]
    size = C.PLANT_STRIDE if kind == "plant" else C.ZOMBIE_STRIDE
    print("盯住 %s[0] @ 0x%08X，采样 %.0f 秒" % (kind, target, seconds))
    samples = []
    t0 = time.time()
    while time.time() - t0 < seconds:
        raw = g.read(target, size)
        if not raw:
            break
        samples.append(struct.unpack("<%dI" % (size // 4), raw))
        time.sleep(0.1)
    if len(samples) < 3:
        print("样本太少")
        return 1
    n = size // 4
    changed = collections.defaultdict(list)
    for i in range(1, len(samples)):
        for k in range(n):
            if samples[i][k] != samples[i - 1][k]:
                changed[k * 4].append(samples[i][k])
    print("\n%-8s %-6s %s" % ("偏移", "次数", "取值序列"))
    for off, vals in sorted(changed.items(), key=lambda kv: -len(kv[1]))[:25]:
        print("+0x%-6X %-6d %s" % (off, len(vals),
                                   " ".join(str(v) for v in vals[:8])))
    return 0


def cmd_armortest(g, stype=1, hp=500):
    """给场上所有僵尸挂一个原生护盾，用来挑「护盾类型」的贴图。

    僵尸护盾是游戏原生机制：
        +0xD8 护盾类型（0=没有盾，非 0 才会吸收伤害）
        +0xDC 护盾当前血量
        +0xE0 护盾最大血量
    伤害流程（0x53184A）会先用 +0xDC 吸收，溢出才扣身体 +0xC8。
    """
    zs = g.scan_zombies()
    if not zs:
        print("场上没有僵尸")
        return 1
    for z in zs:
        a = z["addr"]
        g.write_i32(a + C.Z_SHIELD_TYPE, stype)
        g.write_i32(a + C.Z_SHIELD_MAX, hp)
        g.write_i32(a + C.Z_SHIELD, hp)
    print("已给 %d 个僵尸挂上护盾：类型=%d  血量=%d" % (len(zs), stype, hp))
    print("看看游戏里长什么样；不喜欢就换类型：")
    print("  python tools/hextech.py armortest 2")
    print("  python tools/hextech.py armortest 3")
    print("想清掉就 armortest 0")
    return 0


def cmd_armorclear(g):
    for z in g.scan_zombies():
        a = z["addr"]
        g.write_i32(a + C.Z_SHIELD_TYPE, 0)
        g.write_i32(a + C.Z_SHIELD_MAX, 0)
        g.write_i32(a + C.Z_SHIELD, 0)
    print("已清掉所有僵尸的护盾")
    return 0


def cmd_live(g, seconds=10.0, focus=True, hz=10):
    """实时采样：看哪些字段在动。

    游戏失焦会自动暂停，所以默认先把窗口抢回前台。
    采样期间**不要切走窗口**，否则数据全冻结。
    """
    import collections

    if focus:
        if focus_game(g.pid):
            print("已把游戏切到前台（PVZ 失焦会暂停）。采样 %.0f 秒，期间别切窗口。"
                  % seconds)
        else:
            print("⚠ 抢焦点失败，如果游戏是暂停的，数据不会动。")
    time.sleep(0.4)

    b = g.board
    if not b:
        print("Board 为空，先进入关卡")
        return 1

    board_lo, board_hi = 0x5540, 0x5640
    samples = []          # [(t, {key: value})]
    t0 = time.time()
    while time.time() - t0 < seconds:
        snap = {}
        raw = g.read(b + board_lo, board_hi - board_lo)
        if raw:
            for i in range(0, len(raw), 4):
                snap[("B", 0, board_lo + i)] = struct.unpack_from("<i", raw, i)[0]
        for p in g.scan_plants():
            raw = g.read(p["addr"], C.PLANT_STRIDE)
            if not raw:
                continue
            for i in range(0, C.PLANT_STRIDE, 4):
                snap[("P", p["idx"], i)] = struct.unpack_from("<i", raw, i)[0]
        for z in g.scan_zombies():
            raw = g.read(z["addr"], C.ZOMBIE_STRIDE)
            if not raw:
                continue
            for i in range(0, C.ZOMBIE_STRIDE, 4):
                snap[("Z", z["idx"], i)] = struct.unpack_from("<i", raw, i)[0]
        samples.append((time.time() - t0, snap))
        time.sleep(1.0 / hz)

    if len(samples) < 3:
        print("样本太少")
        return 1

    print("采到 %d 帧，%.1f 秒\n" % (len(samples), samples[-1][0]))

    def report(tag, title):
        changed = collections.defaultdict(list)
        keys = set()
        for _, s in samples:
            keys |= set(k for k in s if k[0] == tag)
        for k in keys:
            seq = [s.get(k) for _, s in samples]
            if any(v is None for v in seq):
                continue
            if len(set(seq)) > 1:
                changed[k].append(seq)
        if not changed:
            print("【%s】整段完全没变（可能游戏是暂停的）" % title)
            return
        print("【%s】%d 个字段在变" % (title, len(changed)))
        rows = sorted(changed.items(),
                      key=lambda kv: -len(set(kv[1][0])))
        for k, seqs in rows[:22]:
            seq = seqs[0]
            uniq = sorted(set(seq))
            head = " ".join(str(v) for v in seq[:8])
            print("  %s%-6s 变化%3d次  取值{%s}  %s"
                  % ("Board+" if tag == "B" else ("P%d+" % k[1] if tag == "P" else "Z%d+" % k[1]),
                     "0x%03X" % k[2], len(set(seq)) - 1,
                     ",".join(str(v) for v in uniq[:6]) + ("…" if len(uniq) > 6 else ""),
                     head))
        print()

    report("B", "Board 0x5540~0x5640")
    report("P", "植物")
    report("Z", "僵尸")
    return 0


# ============================================================ 海克斯面板
def cmd_panel(g, auto=False, selftest=False):
    """独立小窗口：点按钮就列出场上所有植物 / 僵尸带的海克斯。

    为什么不做成覆盖窗口里的按钮 —— 覆盖窗口是点击穿透的
    （WS_EX_TRANSPARENT），收不到鼠标事件。所以按钮必须放在一个正常窗口里。
    """
    import tkinter as tk

    eng = C.Engine(g)
    eng.enabled = False          # 面板只负责看，不结算效果

    root = tk.Tk()
    root.title("PVZ 海克斯面板")
    root.attributes("-topmost", True)
    root.geometry("430x600+%d+%d" % (760, 60))
    root.configure(bg="#151A20")

    bar = tk.Frame(root, bg="#151A20")
    bar.pack(fill="x", padx=10, pady=(10, 4))

    auto_var = tk.BooleanVar(value=auto)

    txt = tk.Text(root, bg="#0E1216", fg="#DCE4EC", insertbackground="#DCE4EC",
                  relief="flat", padx=12, pady=10, wrap="word",
                  font=("Microsoft YaHei", 10), state="disabled")
    txt.pack(fill="both", expand=True, padx=10, pady=(0, 6))

    txt.tag_configure("h", foreground="#7A8896", font=("Microsoft YaHei", 9, "bold"))
    txt.tag_configure("plant", foreground="#6FD08C", font=("Microsoft YaHei", 10, "bold"))
    txt.tag_configure("zombie", foreground="#E8705F", font=("Microsoft YaHei", 10, "bold"))
    txt.tag_configure("dim", foreground="#7A8896")
    txt.tag_configure("silver", foreground="#C8D2DC")
    txt.tag_configure("gold", foreground="#F0B429")
    txt.tag_configure("prismatic", foreground="#B57BFF")
    txt.tag_configure("warn", foreground="#F0B429")

    status = tk.Label(root, text="", bg="#151A20", fg="#7A8896",
                      font=("Microsoft YaHei", 9), anchor="w")
    status.pack(fill="x", padx=12, pady=(0, 10))

    def put(s, tag=None):
        txt.insert("end", s, tag or ())

    def refresh(*_):
        try:
            eng.tick()
        except Exception as e:
            txt.config(state="normal")
            txt.delete("1.0", "end")
            put("读取失败：%s\n\n（游戏是不是已经退出关卡了？）" % e, "warn")
            txt.config(state="disabled")
            status.config(text="读取失败")
            return

        plants = [o for o in eng.objects if o["kind"] == "plant"]
        zombies = [o for o in eng.objects if o["kind"] == "zombie"]

        txt.config(state="normal")
        txt.delete("1.0", "end")

        put("关卡 %s    阳光 %s\n" % (g.level(), g.sun()), "h")
        if eng.seeds:
            put("卡槽 %d 张" % len(eng.seeds), "h")
            if eng.blindbox:
                b = eng.blindbox
                put("　盲盒 = 第 %d 格" % (b["slot"] + 1), "warn")
                put("（卡面固定，种下去随机；本次会种出 %s）\n"
                    % C.PLANT_NAMES.get(b["type"], "?%d" % b["type"]), "dim")
            else:
                put("\n", "h")
        put("每个对象随机 %d~%d 条，不可重复\n\n" % (C.HEX_MIN, C.HEX_MAX), "h")

        put("植物 %d 株\n" % len(plants), "h")
        if not plants:
            put("  （场上没有植物）\n", "dim")
        for p in plants:
            nm = C.PLANT_NAMES.get(p["type"], "?%d" % p["type"])
            put("  %s" % nm, "plant")
            put("  第%d行 第%d列   %d/%d\n"
                % (p["row"] + 1, p["col"] + 1, p["hp"], p["state"].hp_max), "dim")
            if p["hexes"]:
                for hid in p["hexes"]:
                    d = C.HEX_BY_ID[hid]
                    put("      · %s" % d["name"], d["tier"])
                    put("  %s\n" % d["desc"], "dim")
            else:
                put("      （无）\n", "dim")
            put("\n")

        put("僵尸 %d 个\n" % len(zombies), "h")
        if not zombies:
            put("  （场上没有僵尸）\n", "dim")
        for z in zombies:
            nm = C.ZOMBIE_NAMES.get(z["type"], "?%d" % z["type"])
            put("  %s" % nm, "zombie")
            put("  %d/%d\n" % (z["hp"], z["state"].hp_max), "dim")
            for hid in z["hexes"]:
                d = C.HEX_BY_ID[hid]
                put("      · %s" % d["name"], d["tier"])
                put("  %s\n" % d["desc"], "dim")
            put("\n")

        txt.config(state="disabled")
        status.config(text="已刷新 %s   植物 %d · 僵尸 %d"
                      % (time.strftime("%H:%M:%S"), len(plants), len(zombies)))

    def reroll():
        eng.plant_state.clear()
        eng.zombie_state.clear()
        refresh()

    def toggle_auto():
        if auto_var.get():
            loop()
        else:
            status.config(text="自动刷新已关")

    def loop():
        if not auto_var.get():
            return
        refresh()
        root.after(1000, loop)

    btn = tk.Button(bar, text="显示海克斯", command=refresh,
                    bg="#2F7D4F", fg="white", relief="flat",
                    font=("Microsoft YaHei", 10, "bold"), padx=14, pady=6,
                    activebackground="#3B9A62", activeforeground="white",
                    cursor="hand2")
    btn.pack(side="left")

    tk.Button(bar, text="重新抽", command=reroll,
              bg="#2A333C", fg="#DCE4EC", relief="flat",
              font=("Microsoft YaHei", 9), padx=10, pady=6,
              activebackground="#3A4550", activeforeground="white",
              cursor="hand2").pack(side="left", padx=(8, 0))

    tk.Checkbutton(bar, text="自动刷新", variable=auto_var, command=toggle_auto,
                   bg="#151A20", fg="#DCE4EC", selectcolor="#2A333C",
                   activebackground="#151A20", activeforeground="#DCE4EC",
                   font=("Microsoft YaHei", 9)).pack(side="right")

    root.bind("<F5>", refresh)
    refresh()
    if selftest:
        print("--- 面板渲染内容 ---")
        print(txt.get("1.0", "end").rstrip())
        print("--- 结束 ---")
        root.destroy()
        return 0
    if auto:
        loop()
    print("面板已打开。点「显示海克斯」或按 F5 刷新；关掉窗口即退出。")
    root.mainloop()
    return 0


# ============================================================ main
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    g = C.Game()
    try:
        if cmd == "calibrate":
            return cmd_calibrate(g)
        if cmd == "watchdiff":
            kind = sys.argv[2] if len(sys.argv) > 2 else "zombie"
            secs = float(sys.argv[3]) if len(sys.argv) > 3 else 15.0
            return cmd_watchdiff(g, kind, secs)
        if cmd == "panel":
            return cmd_panel(g, "--auto" in sys.argv, "--selftest" in sys.argv)
        if cmd == "live":
            secs = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
            hz = int(sys.argv[3]) if len(sys.argv) > 3 else 10
            return cmd_live(g, secs, "--nofocus" not in sys.argv, hz)
        if cmd == "armortest":
            stype = int(sys.argv[2]) if len(sys.argv) > 2 else 1
            if stype == 0:
                return cmd_armorclear(g)
            hp = int(sys.argv[3]) if len(sys.argv) > 3 else 500
            return cmd_armortest(g, stype, hp)
        if cmd == "probe":
            secs = float(sys.argv[2]) if len(sys.argv) > 2 else 0
            C.probe_once(g)
            if secs > 0:
                eng = C.Engine(g)
                t0 = time.time()
                while time.time() - t0 < secs:
                    eng.tick()
                    time.sleep(1 / 20)
                    if int((time.time() - t0) * 2) != int((time.time() - t0 - 0.05) * 2):
                        sys.stdout.write("\r植物 %d  僵尸 %d  击杀 %d  "
                                         % (eng.stats["plant"],
                                            eng.stats["zombie"],
                                            eng.stats["kills"]))
                        sys.stdout.flush()
                print()
            return 0
        if cmd == "run":
            eng = C.Engine(g)
            if "--dry" in sys.argv:
                eng.enabled = False
            ov = Overlay(eng, g)
            ov.dry = "--dry" in sys.argv
            if "--geom" in sys.argv:
                i = sys.argv.index("--geom")
                if i + 1 < len(sys.argv):
                    ov.geom_override = sys.argv[i + 1]
            shot = None
            after = 2.0
            if "--shot" in sys.argv:
                i = sys.argv.index("--shot")
                if i + 1 < len(sys.argv):
                    shot = sys.argv[i + 1]
            if "--after" in sys.argv:
                i = sys.argv.index("--after")
                if i + 1 < len(sys.argv):
                    after = float(sys.argv[i + 1])
            if not shot:
                print("覆盖窗口已启动：F7 血量 / F8 详情 / F9 开关 / F10 重抽。Ctrl+C 退出。")
            ov.run(shot, after)
            return 0
        print(__doc__)
        return 1
    finally:
        g.close()


if __name__ == "__main__":
    sys.exit(main())
