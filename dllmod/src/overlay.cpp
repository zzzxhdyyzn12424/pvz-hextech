// ============================================================================
//  overlay.cpp —— 覆盖层（对齐外置版 v1.7 的显示）
//
//  三块内容，和外置版一模一样：
//    1. 场上血量：植物头顶数字；僵尸三层血条（盾蓝 / 头盔黄 / 本体红）
//    2. 头顶词条卡：每个对象上方一排 17×17 小方块，写词条短名，按档位上色
//    3. 右侧详情面板：按钮 / F8 开关，列出所有对象 + 词条全名
//
//  实现方式：一个覆盖整个客户区的分层窗口，用**颜色键**做透明
//  （SetLayeredWindowAttributes + LWA_COLORKEY，键色 RGB(1,2,3)）。
//  外置版用 tkinter 的 -transparentcolor 是同一套机制，所以视觉上一致。
//
//  为什么不用 UpdateLayeredWindow 的逐像素 alpha：
//  那要求颜色预乘 alpha，而 GDI 画出来的字没法预乘，会变成发白的糊字。
//  颜色键唯一的小毛病是抗锯齿边缘会带一圈键色 —— 所以场上文字用
//  NONANTIALIASED 字体（不抗锯齿就没有过渡色）；面板里的字坐在自己的
//  深色底上，可以放心抗锯齿。
// ============================================================================
#include "overlay.h"

#include <windows.h>

#include <string>
#include <vector>

#include "engine.h"
#include "hex_table.gen.h"
#include "log.h"

namespace hx {
namespace {

// ---- 和外置版 tools/hextech.py 的常量一一对应 ----
constexpr COLORREF C_KEY      = RGB(1, 2, 3);      // TRANSPARENT_KEY "#010203"
constexpr COLORREF C_SHIELD   = RGB(0x4A, 0x90, 0xE2);
constexpr COLORREF C_HELMET   = RGB(0xF0, 0xB4, 0x29);
constexpr COLORREF C_BODY     = RGB(0xE2, 0x4A, 0x4A);
constexpr COLORREF C_BAR_BG   = RGB(0x14, 0x19, 0x1F);
constexpr COLORREF C_HP_TEXT  = RGB(0xE8, 0xEE, 0xF5);
constexpr COLORREF C_HP_MID   = RGB(0xF0, 0xB4, 0x29);
constexpr COLORREF C_HP_LOW   = RGB(0xFF, 0x5A, 0x5A);
constexpr COLORREF C_CARD_EDGE = RGB(0x10, 0x16, 0x1C);
constexpr COLORREF C_CARD_TEXT = RGB(0x0B, 0x0E, 0x12);

constexpr int HP_BAR_W   = 46;
constexpr int HP_BAR_H   = 3;
constexpr int HP_BAR_GAP = 1;
constexpr int CARD_H     = 17;
constexpr int CARD_STEP  = CARD_H + 3;

constexpr wchar_t kBtnClass[]     = L"hxHexBtnWnd";
constexpr wchar_t kOverlayClass[] = L"hxHexOverlayWnd";

constexpr int kBtnW = 92;
constexpr int kBtnH = 26;

constexpr int VK_F7_ = 0x76;
constexpr int VK_F8_ = 0x77;

HWND g_game    = nullptr;
HWND g_btn     = nullptr;
HWND g_overlay = nullptr;

bool g_show_hp    = true;    // F7
bool g_show_panel = false;   // 按钮 / F8

HFONT g_font_world = nullptr;   // 场上数字：不抗锯齿（避免键色描边）
HFONT g_font_panel = nullptr;   // 面板正文
HFONT g_font_card  = nullptr;   // 词条卡里的单字（17px 格子，必须比面板字更小）
HFONT g_font_head  = nullptr;   // 面板标题

std::wstring W(const std::string& s) {
    if (s.empty()) return std::wstring();
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
    if (n <= 0) return std::wstring();
    std::wstring out((size_t)n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), &out[0], n);
    return out;
}

COLORREF TierColor(Tier t) {
    switch (t) {
    case Tier::Gold:      return RGB(0xF0, 0xB4, 0x29);
    case Tier::Prismatic: return RGB(0xB5, 0x7B, 0xFF);
    default:              return RGB(0xC8, 0xD2, 0xDC);
    }
}

COLORREF HpColor(int cur, int mx) {
    double r = mx ? (double)cur / mx : 0.0;
    if (r <= 0.25) return C_HP_LOW;
    if (r <= 0.50) return C_HP_MID;
    return C_HP_TEXT;
}

// ---- 找游戏窗口 ----
struct FindCtx { DWORD pid; HWND best; int area; };

// 必须 CALLBACK(__stdcall)：MinGW 下无捕获 lambda 转函数指针是 cdecl
BOOL CALLBACK FindProc(HWND h, LPARAM lp) {
    FindCtx* c = (FindCtx*)lp;
    DWORD pid = 0;
    GetWindowThreadProcessId(h, &pid);
    if (pid != c->pid) return TRUE;
    if (!IsWindowVisible(h)) return TRUE;
    RECT r;
    if (!GetClientRect(h, &r)) return TRUE;
    int a = (r.right - r.left) * (r.bottom - r.top);
    if (a > c->area) { c->area = a; c->best = h; }
    return TRUE;
}

HWND FindGameWindow() {
    FindCtx ctx{ GetCurrentProcessId(), nullptr, 0 };
    EnumWindows(FindProc, (LPARAM)&ctx);
    return ctx.best;
}

void ClampToWorkArea(HWND ref, int& x, int& y, int w, int h) {
    HMONITOR mon = MonitorFromWindow(ref, MONITOR_DEFAULTTONEAREST);
    MONITORINFO mi = {};
    mi.cbSize = sizeof(mi);
    if (!GetMonitorInfoW(mon, &mi)) return;
    const RECT& wa = mi.rcWork;
    if (x + w > wa.right)  x = wa.right - w - 4;
    if (y + h > wa.bottom) y = wa.bottom - h - 4;
    if (x < wa.left) x = wa.left + 4;
    if (y < wa.top)  y = wa.top + 4;
}

// ===========================================================================
//  绘制
// ===========================================================================
// ★ swprintf 里的 %s 陷阱（我踩了）：
// MinGW-w64 的 swprintf 沿用 MSVC 语义 —— 宽格式串里的 %s 取的是**窄字符串**，
// 宽字符串必须写 %ls。写成 %s 不会报错，但会把 UTF-16 的两个字节当成两个
// ANSI 字符重新解码，中文全变成 "oTāeu" 这种乱码。字面量 L"..." 直接传的
// 参数不受影响，所以现象是「有的字正常、有的字乱」。
void TextAt(HDC dc, int x, int y, const std::wstring& s, COLORREF col, HFONT f) {
    SelectObject(dc, f);
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, col);
    TextOutW(dc, x, y, s.c_str(), (int)s.size());
}

void TextCenterBottom(HDC dc, int cx, int bottom, const std::wstring& s,
                      COLORREF col, HFONT f) {
    SelectObject(dc, f);
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, col);
    SIZE sz;
    GetTextExtentPoint32W(dc, s.c_str(), (int)s.size(), &sz);
    TextOutW(dc, cx - sz.cx / 2, bottom - sz.cy, s.c_str(), (int)s.size());
}

void Fill(HDC dc, int l, int t, int r, int b, COLORREF col) {
    HBRUSH br = CreateSolidBrush(col);
    RECT rc{ l, t, r, b };
    FillRect(dc, &rc, br);
    DeleteObject(br);
}

// ---- 场上血量（对应外置版 _draw_hp）----
void DrawHp(HDC dc, const Engine::OverlaySnap& s) {
    for (const Engine::OverlayObj& o : s.objs) {
        int x = o.x, y = o.y;
        if (x < -80 || x > 900 || y < -40 || y > 660) continue;

        if (o.plant) {
            int mx = o.hp_max > 0 ? o.hp_max : (o.hp > 0 ? o.hp : 1);
            int cx = x + (o.w > 0 ? o.w : 80) / 2;
            wchar_t buf[24];
            swprintf(buf, 24, L"%d", o.hp);
            TextCenterBottom(dc, cx, y - 8, buf, HpColor(o.hp, mx), g_font_world);
            continue;
        }

        // 僵尸：盾 / 头盔 / 本体，从上往下叠
        int mx_body = o.hp_max > 0 ? o.hp_max : (o.hp > 0 ? o.hp : 1);
        int cur[3], mx[3];
        COLORREF col[3];
        int n = 0;
        if (o.shield_max > 0) { cur[n] = o.shield; mx[n] = o.shield_max; col[n] = C_SHIELD; n++; }
        if (o.helmet_max > 0) { cur[n] = o.helmet; mx[n] = o.helmet_max; col[n] = C_HELMET; n++; }
        cur[n] = o.hp; mx[n] = mx_body; col[n] = C_BODY; n++;

        int block_h = n * HP_BAR_H + (n - 1) * HP_BAR_GAP;
        int cx = x + (o.w > 0 ? o.w : 60) / 2;
        int x0 = cx - HP_BAR_W / 2;
        int top = y - 8 - block_h;
        for (int i = 0; i < n; ++i) {
            int yy = top + i * (HP_BAR_H + HP_BAR_GAP);
            Fill(dc, x0, yy, x0 + HP_BAR_W, yy + HP_BAR_H, C_BAR_BG);
            if (mx[i] > 0 && cur[i] > 0) {
                double f = (double)cur[i] / mx[i];
                if (f > 1.0) f = 1.0;
                int fw = (int)(HP_BAR_W * f);
                if (fw < 1) fw = 1;
                Fill(dc, x0, yy, x0 + fw, yy + HP_BAR_H, col[i]);
            }
        }
    }
}

// ---- 头顶词条卡（对应 _draw_cards）----
void DrawCards(HDC dc, const Engine::OverlaySnap& s) {
    for (const Engine::OverlayObj& o : s.objs) {
        if (o.nhex <= 0) continue;
        int w = o.w > 0 ? o.w : 80;
        int cx = o.x + w / 2;
        int y = o.y - (g_show_hp ? 38 : 20);
        if (cx < -60 || cx > 900 || y < -40 || y > 640) continue;

        int total_w = o.nhex * CARD_STEP - 3;
        int x0 = cx - total_w / 2;
        for (int i = 0; i < o.nhex; ++i) {
            int hid = o.hex[i];
            if (hid < 0 || hid >= kHexDefCount) continue;
            const HexDef& d = kHexDefs[hid];
            int cx0 = x0 + i * CARD_STEP;

            Fill(dc, cx0, y, cx0 + CARD_H, y + CARD_H, TierColor(d.tier));
            Fill(dc, cx0, y, cx0 + CARD_H, y + 1, C_CARD_EDGE);
            Fill(dc, cx0, y + CARD_H - 1, cx0 + CARD_H, y + CARD_H, C_CARD_EDGE);
            Fill(dc, cx0, y, cx0 + 1, y + CARD_H, C_CARD_EDGE);
            Fill(dc, cx0 + CARD_H - 1, y, cx0 + CARD_H, y + CARD_H, C_CARD_EDGE);

            std::wstring sh = W(d.short_name);
            SelectObject(dc, g_font_card);
            SIZE sz;
            GetTextExtentPoint32W(dc, sh.c_str(), (int)sh.size(), &sz);
            // -1：GetTextExtent 给的是「行高」不是「字墨高」，直接居中会让
            // 下半截压到格子边线上（实测「灵」「力」都被切了底）。
            TextAt(dc, cx0 + (CARD_H - sz.cx) / 2, y + (CARD_H - sz.cy) / 2 - 1,
                   sh, C_CARD_TEXT, g_font_card);
        }
    }
}

// ---- 详情面板（对应 _draw_panel）----
void DrawPanel(HDC dc, const Engine::OverlaySnap& s, int w, int h) {
    int pw = 520;
    int ph = h - 40;
    if (ph > 560) ph = 560;
    if (pw > w - 28) pw = w - 28;
    if (ph < 120) return;
    int px = w - pw - 14;
    int py = 14;

    Fill(dc, px, py, px + pw, py + ph, RGB(0x0E, 0x12, 0x16));
    Fill(dc, px, py, px + pw, py + 1, RGB(0xC8, 0xD2, 0xDC));
    Fill(dc, px, py + ph - 1, px + pw, py + ph, RGB(0xC8, 0xD2, 0xDC));
    Fill(dc, px, py, px + 1, py + ph, RGB(0xC8, 0xD2, 0xDC));
    Fill(dc, px + pw - 1, py, px + pw, py + ph, RGB(0xC8, 0xD2, 0xDC));

    int tx = px + 10;
    int ty = py + 8;
    const int lh = 18;
    wchar_t buf[256];

    swprintf(buf, 256, L"海克斯版   关卡 %d   阳光 %d", s.level, s.sun);
    TextAt(dc, tx, ty, buf, RGB(0xE8, 0xEE, 0xF5), g_font_head);
    ty += lh + 4;

    swprintf(buf, 256, L"随机 1~3 条 · 植物 %d 株 · 僵尸 %d 个", s.plants, s.zombies);
    TextAt(dc, tx, ty, buf, RGB(0x9A, 0xA4, 0xB2), g_font_panel);
    ty += lh;

    Fill(dc, tx, ty + 4, px + pw - 10, ty + 5, RGB(0x3A, 0x42, 0x4E));
    ty += lh;

    int lines = (py + ph - 10 - ty) / lh;

    auto emit = [&](const std::wstring& line, COLORREF col) -> bool {
        if (lines <= 0) return false;
        TextAt(dc, tx, ty, line, col, g_font_panel);
        ty += lh;
        --lines;
        return true;
    };

    int total_z = 0;
    for (const Engine::OverlayObj& o : s.objs) if (!o.plant) total_z++;

    bool plant_head = false, zombie_head = false;
    int shown_z = 0;

    for (const Engine::OverlayObj& o : s.objs) {
        if (o.plant && !plant_head) {
            plant_head = true;
            if (!emit(L"【植物】", RGB(0x6E, 0xBE, 0x8C))) break;
        }
        if (!o.plant && !zombie_head) {
            zombie_head = true;
            if (!emit(L"【僵尸】", RGB(0xD2, 0x78, 0x78))) break;
        }

        std::string nm;
        if (o.plant) {
            nm = (o.type >= 0 && o.type < kPlantNamesCount && kPlantNames[o.type][0])
                 ? kPlantNames[o.type] : ("?" + std::to_string(o.type));
        } else {
            nm = (o.type >= 0 && o.type < kZombieNamesCount && kZombieNames[o.type][0])
                 ? kZombieNames[o.type] : ("?" + std::to_string(o.type));
        }

        std::string hx;
        for (int i = 0; i < o.nhex; ++i) {
            int hid = o.hex[i];
            if (hid < 0 || hid >= kHexDefCount) continue;
            if (!hx.empty()) hx += "/";
            hx += kHexDefs[hid].name;
        }

        if (o.plant) {
            swprintf(buf, 256, L"  %ls(%d,%d)  %ls", W(nm).c_str(), o.row, o.col,
                     W(hx).c_str());
            if (!emit(buf, RGB(0xC8, 0xD2, 0xDC))) break;
        } else {
            if (shown_z >= 24) continue;
            swprintf(buf, 256, L"  %ls  %d/%d  %ls", W(nm).c_str(), o.hp,
                     o.hp_max, W(hx).c_str());
            if (!emit(buf, RGB(0xC8, 0xD2, 0xDC))) break;
            shown_z++;
        }
    }
    if (total_z > shown_z && lines > 0) {
        swprintf(buf, 256, L"  …还有 %d 个", total_z - shown_z);
        emit(buf, RGB(0x9A, 0xA4, 0xB2));
    }
    // 底部提示（外置版这里有 F8/F9/F10 三行，dllmod 只有 F7/F8）
    if (lines > 0) {
        Fill(dc, tx, ty + 4, px + pw - 10, ty + 5, RGB(0x3A, 0x42, 0x4E));
        ty += lh;
        emit(L"F7 血量   F8 面板", RGB(0x6E, 0x78, 0x88));
    }
}

// ===========================================================================
//  窗口过程
// ===========================================================================
LRESULT CALLBACK OverlayProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    if (msg != WM_PAINT) return DefWindowProcW(hwnd, msg, wp, lp);

    PAINTSTRUCT ps;
    HDC dc = BeginPaint(hwnd, &ps);
    RECT rc;
    GetClientRect(hwnd, &rc);
    int w = rc.right, h = rc.bottom;
    if (w <= 0 || h <= 0) { EndPaint(hwnd, &ps); return 0; }

    // 双缓冲，避免每帧闪
    HDC mem = CreateCompatibleDC(dc);
    HBITMAP bmp = CreateCompatibleBitmap(dc, w, h);
    HGDIOBJ old = SelectObject(mem, bmp);

    Fill(mem, 0, 0, w, h, C_KEY);        // 键色 = 全透明

    Engine::OverlaySnap snap;
    GetEngine().CopyOverlay(snap);

    if (!snap.objs.empty()) {
        if (g_show_hp) DrawHp(mem, snap);
        DrawCards(mem, snap);
        if (g_show_panel) DrawPanel(mem, snap, w, h);
    }

    BitBlt(dc, 0, 0, w, h, mem, 0, 0, SRCCOPY);
    SelectObject(mem, old);
    DeleteObject(bmp);
    DeleteDC(mem);
    EndPaint(hwnd, &ps);
    return 0;
}

void PaintButton(HWND hwnd) {
    PAINTSTRUCT ps;
    HDC dc = BeginPaint(hwnd, &ps);
    RECT rc;
    GetClientRect(hwnd, &rc);

    HBRUSH bg = CreateSolidBrush(g_show_panel ? RGB(58, 44, 96) : RGB(30, 32, 42));
    FillRect(dc, &rc, bg);
    DeleteObject(bg);

    HPEN pen = CreatePen(PS_SOLID, 1,
                         g_show_panel ? RGB(150, 120, 235) : RGB(80, 84, 100));
    HGDIOBJ oldPen = SelectObject(dc, pen);
    HGDIOBJ oldBrush = SelectObject(dc, GetStockObject(NULL_BRUSH));
    Rectangle(dc, rc.left, rc.top, rc.right, rc.bottom);
    SelectObject(dc, oldBrush);
    SelectObject(dc, oldPen);
    DeleteObject(pen);

    SelectObject(dc, g_font_panel);
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, g_show_panel ? RGB(230, 220, 255) : RGB(190, 196, 210));
    DrawTextW(dc, g_show_panel ? L"海克斯 ▲" : L"海克斯 ▼", -1, &rc,
              DT_CENTER | DT_VCENTER | DT_SINGLELINE);

    EndPaint(hwnd, &ps);
}

void TogglePanel() {
    g_show_panel = !g_show_panel;
    if (g_btn) InvalidateRect(g_btn, nullptr, TRUE);
    Log("[ui] 详情面板 %s", g_show_panel ? "开" : "关");
}

LRESULT CALLBACK BtnProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_PAINT:      PaintButton(hwnd); return 0;
    case WM_LBUTTONUP:  TogglePanel();     return 0;
    case WM_SETCURSOR:
        SetCursor(LoadCursorW(nullptr, MAKEINTRESOURCEW(32649)));  // IDC_HAND
        return TRUE;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

void CreateWindows() {
    HINSTANCE hi = GetModuleHandleW(nullptr);

    WNDCLASSEXW wc = {};
    wc.cbSize        = sizeof(wc);
    wc.lpfnWndProc   = OverlayProc;
    wc.hInstance     = hi;
    wc.hCursor       = LoadCursorW(nullptr, MAKEINTRESOURCEW(32512));  // IDC_ARROW
    wc.lpszClassName = kOverlayClass;
    RegisterClassExW(&wc);

    g_overlay = CreateWindowExW(
        WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST |
        WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        kOverlayClass, L"", WS_POPUP, 0, 0, 800, 600,
        nullptr, nullptr, hi, nullptr);
    // 颜色键透明：键色像素完全不显示（外置版 tkinter -transparentcolor 同机制）
    SetLayeredWindowAttributes(g_overlay, C_KEY, 0, LWA_COLORKEY);

    wc.lpfnWndProc   = BtnProc;
    wc.lpszClassName = kBtnClass;
    RegisterClassExW(&wc);

    g_btn = CreateWindowExW(
        WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        kBtnClass, L"", WS_POPUP, 0, 0, kBtnW, kBtnH,
        nullptr, nullptr, hi, nullptr);

    g_font_world = CreateFontW(-12, 0, 0, 0, FW_BOLD, 0, 0, 0, DEFAULT_CHARSET,
                               OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                               NONANTIALIASED_QUALITY, DEFAULT_PITCH,
                               L"Microsoft YaHei");
    g_font_panel = CreateFontW(-13, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET,
                               OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                               ANTIALIASED_QUALITY, DEFAULT_PITCH,
                               L"Microsoft YaHei");
    g_font_head = CreateFontW(-16, 0, 0, 0, FW_BOLD, 0, 0, 0, DEFAULT_CHARSET,
                              OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                              ANTIALIASED_QUALITY, DEFAULT_PITCH,
                              L"Microsoft YaHei");
    g_font_card = CreateFontW(-11, 0, 0, 0, FW_BOLD, 0, 0, 0, DEFAULT_CHARSET,
                              OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                              ANTIALIASED_QUALITY, DEFAULT_PITCH,
                              L"Microsoft YaHei");

    Log("[ui] 覆盖层已创建 overlay=%p btn=%p", (void*)g_overlay, (void*)g_btn);
}

DWORD WINAPI UiThread(LPVOID) {
    for (int i = 0; i < 300 && !g_game; ++i) {
        g_game = FindGameWindow();
        if (!g_game) Sleep(200);
    }
    if (!g_game) {
        Log("[ui] 找不到游戏窗口，覆盖层不启动");
        return 0;
    }
    CreateWindows();

    bool f7 = false, f8 = false;
    for (;;) {
        // ★ 必须泵消息：不泵的话 WM_PAINT 永远不派发（按钮会是一块黑板）。
        MSG msg;
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }

        bool k7 = (GetAsyncKeyState(VK_F7_) & 0x8000) != 0;
        bool k8 = (GetAsyncKeyState(VK_F8_) & 0x8000) != 0;
        if (k7 && !f7) { g_show_hp = !g_show_hp; Log("[ui] 血量 %s", g_show_hp ? "开" : "关"); }
        if (k8 && !f8) TogglePanel();
        f7 = k7;
        f8 = k8;

        Sleep(33);      // ≈30fps，和外置版一致

        if (!IsWindow(g_game)) {
            g_game = FindGameWindow();
            if (!g_game) continue;
        }
        if (IsIconic(g_game) || !IsWindowVisible(g_game)) {
            ShowWindow(g_overlay, SW_HIDE);
            ShowWindow(g_btn, SW_HIDE);
            continue;
        }
        // 游戏不在前台就收起来。
        // 覆盖层是 TOPMOST 的，不收的话你切去别的窗口，血条和词条卡还会
        // 飘在那个窗口上面 —— 看着像见鬼。
        {
            HWND fg = GetForegroundWindow();
            if (fg != g_game && GetWindow(fg, GW_OWNER) != g_game) {
                ShowWindow(g_overlay, SW_HIDE);
                ShowWindow(g_btn, SW_HIDE);
                continue;
            }
        }

        RECT cr;
        GetClientRect(g_game, &cr);
        POINT origin{ 0, 0 };
        ClientToScreen(g_game, &origin);
        int cw = cr.right - cr.left;
        int ch = cr.bottom - cr.top;
        if (cw < 200 || ch < 200) continue;

        // 覆盖层铺满客户区
        SetWindowPos(g_overlay, HWND_TOPMOST, origin.x, origin.y, cw, ch,
                     SWP_NOACTIVATE | SWP_SHOWWINDOW);
        InvalidateRect(g_overlay, nullptr, FALSE);
        UpdateWindow(g_overlay);

        // 按钮贴在客户区右下角（钳进工作区，窗口可能有一截在屏幕外）
        int bx = origin.x + cw - kBtnW - 8;
        int by = origin.y + ch - kBtnH - 8;
        ClampToWorkArea(g_game, bx, by, kBtnW, kBtnH);
        SetWindowPos(g_btn, HWND_TOPMOST, bx, by, kBtnW, kBtnH,
                     SWP_NOACTIVATE | SWP_SHOWWINDOW);
    }
    return 0;
}

}  // namespace

void OverlayStart() {
    HANDLE t = CreateThread(nullptr, 0, UiThread, nullptr, 0, nullptr);
    if (t) CloseHandle(t);
}

}  // namespace hx
