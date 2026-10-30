// ============================================================================
//  overlay.cpp —— 词条面板
//
//  为什么用分层窗口而不是「画进游戏自己的表面」：
//  PVZ 用 SexyAppFramework 往 DirectDraw 表面画，混进去要先逆向它的
//  Graphics::DrawString + 字体表。面板只是给自己看的信息，不值得为它翻
//  整个渲染管线。叠一层窗口视觉上没区别，而且完全不碰游戏渲染，风险为零。
//
//  两个窗口：
//    · 按钮  右下角小方块，可点击（会吃掉那一小块的鼠标，所以做得很小）
//    · 面板  分层 + WS_EX_TRANSPARENT，纯展示，点击穿透到游戏
// ============================================================================
#include "overlay.h"

#include <windows.h>

#include <string>
#include <vector>

#include "engine.h"
#include "log.h"

namespace hx {
namespace {

constexpr wchar_t kBtnClass[]   = L"hxHexBtnWnd";
constexpr wchar_t kPanelClass[] = L"hxHexPanelWnd";

constexpr int kBtnW = 92;
constexpr int kBtnH = 26;
constexpr int kPad  = 12;
constexpr int kLine = 19;
constexpr int kHead = 24;

HWND g_game  = nullptr;
HWND g_btn   = nullptr;
HWND g_panel = nullptr;
bool g_visible = false;

HDC     g_memdc = nullptr;
HBITMAP g_bmp   = nullptr;
void*   g_bits  = nullptr;
int     g_w = 0, g_h = 0;

HFONT g_font      = nullptr;
HFONT g_font_head = nullptr;

// UTF-8 -> UTF-16。工程里的字符串全是 UTF-8（源文件就是 UTF-8），
// GDI 的宽字符版要 UTF-16，中间转一道。别用 TextOutA —— 那会走 ANSI
// 代码页，中文版 Windows 上是 GBK，UTF-8 直接变乱码。
std::wstring W(const std::string& s) {
    if (s.empty()) return std::wstring();
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
    if (n <= 0) return std::wstring();
    std::wstring out((size_t)n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), &out[0], n);
    return out;
}

// 找游戏主窗口：本进程里面积最大的可见顶层窗口
struct FindCtx {
    DWORD pid;
    HWND  best;
    int   area;
};

// 注意必须是 __stdcall（CALLBACK）：MinGW 下无捕获 lambda 转函数指针是 cdecl，
// 直接传给 EnumWindows 会编译不过（WNDENUMPROC 要求 stdcall）。
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

void EnsureDib(int w, int h) {
    if (g_memdc && g_w == w && g_h == h) return;
    if (g_bmp)   { DeleteObject(g_bmp); g_bmp = nullptr; }
    if (g_memdc) { DeleteDC(g_memdc);   g_memdc = nullptr; }
    g_bits = nullptr;

    HDC screen = GetDC(nullptr);
    g_memdc = CreateCompatibleDC(screen);

    BITMAPINFO bi = {};
    bi.bmiHeader.biSize        = sizeof(BITMAPINFOHEADER);
    bi.bmiHeader.biWidth       = w;
    bi.bmiHeader.biHeight      = -h;          // 负数 = 自上而下
    bi.bmiHeader.biPlanes      = 1;
    bi.bmiHeader.biBitCount    = 32;
    bi.bmiHeader.biCompression = BI_RGB;

    void* bits = nullptr;
    g_bmp = CreateDIBSection(screen, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
    if (g_bmp) SelectObject(g_memdc, g_bmp);
    ReleaseDC(nullptr, screen);

    g_bits = bits;
    g_w = w;
    g_h = h;
}

// 32bpp DIB 的内存顺序是 B,G,R,A（小端），所以 0xAARRGGBB 直接写就行
void Fill(uint32_t argb) {
    if (!g_bits) return;
    uint32_t* p = (uint32_t*)g_bits;
    int n = g_w * g_h;
    for (int i = 0; i < n; ++i) p[i] = argb;
}

void Text(int x, int y, const std::wstring& s, COLORREF color, bool head) {
    if (!g_memdc) return;
    SelectObject(g_memdc, head ? g_font_head : g_font);
    SetBkMode(g_memdc, TRANSPARENT);
    SetTextColor(g_memdc, color);
    TextOutW(g_memdc, x, y, s.c_str(), (int)s.size());
}

void Toggle();

void PaintButton(HWND hwnd) {
    PAINTSTRUCT ps;
    HDC dc = BeginPaint(hwnd, &ps);
    RECT rc;
    GetClientRect(hwnd, &rc);

    // 深色底 + 细边，跟面板一套配色
    HBRUSH bg = CreateSolidBrush(g_visible ? RGB(58, 44, 96) : RGB(30, 32, 42));
    FillRect(dc, &rc, bg);
    DeleteObject(bg);
    HPEN pen = CreatePen(PS_SOLID, 1, g_visible ? RGB(150, 120, 235) : RGB(80, 84, 100));
    HGDIOBJ oldPen = SelectObject(dc, pen);
    HGDIOBJ oldBrush = SelectObject(dc, GetStockObject(NULL_BRUSH));
    Rectangle(dc, rc.left, rc.top, rc.right, rc.bottom);
    SelectObject(dc, oldBrush);
    SelectObject(dc, oldPen);
    DeleteObject(pen);

    SelectObject(dc, g_font);
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, g_visible ? RGB(230, 220, 255) : RGB(190, 196, 210));
    const wchar_t* label = g_visible ? L"海克斯 ▲" : L"海克斯 ▼";
    DrawTextW(dc, label, -1, &rc, DT_CENTER | DT_VCENTER | DT_SINGLELINE);

    EndPaint(hwnd, &ps);
}

LRESULT CALLBACK BtnProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_PAINT:
        PaintButton(hwnd);
        return 0;
    case WM_LBUTTONUP:
        Toggle();
        return 0;
    case WM_SETCURSOR:
        SetCursor(LoadCursorW(nullptr, MAKEINTRESOURCEW(32649)));  // IDC_HAND
        return TRUE;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

void RenderPanel(HWND hwnd, int sx, int sy, int cw, int ch) {
    std::vector<Engine::OverlayRow> rows;
    int np = 0, nz = 0;
    uint32_t age = 0;
    GetEngine().CopyOverlay(rows, np, nz, age);

    int n = (int)rows.size();
    int w = 470;
    int h = kPad * 2 + kHead * 2 + n * kLine + 6;
    if (w > cw - 24) w = cw - 24;
    if (h > ch - 24) h = ch - 24;
    if (w < 200 || h < 80) return;

    EnsureDib(w, h);
    if (!g_bits) return;
    Fill(0xF2181A24);          // 深色，A=0xF2（UpdateLayeredWindow 里还会再乘一次）

    int x = kPad;
    int y = kPad;

    char buf[128];
    wsprintfA(buf, "植物 %d   僵尸 %d", np, nz);
    Text(x, y, L"海克斯", RGB(180, 150, 255), true);
    Text(x + 58, y + 2, W(buf), RGB(150, 156, 172), false);
    y += kHead;

    if (n == 0) {
        Text(x, y, W(age > 3000 ? "（还没进入关卡）" : "（场上没有对象）"),
             RGB(120, 126, 140), false);
    }

    bool wrote_plant_head = false, wrote_zombie_head = false;
    int shown = 0;
    for (const Engine::OverlayRow& r : rows) {
        if (y + kLine > h - kPad) break;      // 放不下就截断
        if (r.plant && !wrote_plant_head) {
            wrote_plant_head = true;
            Text(x, y, L"── 植物 ──", RGB(110, 190, 140), false);
            y += kLine;
            if (y + kLine > h - kPad) break;
        }
        if (!r.plant && !wrote_zombie_head) {
            wrote_zombie_head = true;
            if (wrote_plant_head) y += 4;
            Text(x, y, L"── 僵尸 ──", RGB(210, 120, 120), false);
            y += kLine;
            if (y + kLine > h - kPad) break;
        }

        wsprintfA(buf, "%d  %s", r.idx, r.name.c_str());
        Text(x, y, W(buf), RGB(215, 220, 232), false);
        // 血量写成 270/270+81：+81 是原生护盾槽里的值，
        // 不带这一项就看不出「山脉龙魂 / 逃跑计划」到底装上没有
        if (r.shield > 0) wsprintfA(buf, "%d/%d+%d", r.hp, r.hp_max, r.shield);
        else              wsprintfA(buf, "%d/%d", r.hp, r.hp_max);
        Text(x + 150, y, W(buf), RGB(160, 200, 240), false);
        Text(x + 235, y, W(r.hexes), RGB(240, 200, 120), false);
        y += kLine;
        ++shown;
    }
    if (shown < n && y + kLine <= h - kPad) {
        wsprintfA(buf, "…… 还有 %d 个", n - shown);
        Text(x, y, W(buf), RGB(120, 126, 140), false);
    }

    POINT src{ 0, 0 };
    SIZE  sz{ w, h };
    POINT dst{ sx, sy };
    // AlphaFormat = 0 + SourceConstantAlpha：整窗统一半透明。
    // 不用 AC_SRC_ALPHA，是因为那要求颜色预乘 alpha，GDI 画出来的字
    // 没法预乘，会变成发白的糊字。
    BLENDFUNCTION bf = { AC_SRC_OVER, 0, 210, 0 };
    UpdateLayeredWindow(hwnd, nullptr, &dst, &sz, g_memdc, &src, 0, &bf, ULW_ALPHA);
}

// 把窗口钳进显示器工作区。
// 为什么需要：玩家的游戏窗口不一定是完整可见的（截图里这只窗口底部就
// 伸到屏幕外面去了），照「客户区右下角」算出来的按钮会跟着跑到屏幕外，
// 玩家根本点不到。
void ClampToWorkArea(HWND ref, int& x, int& y, int w, int h) {
    HMONITOR mon = MonitorFromWindow(ref, MONITOR_DEFAULTTONEAREST);
    MONITORINFO mi = {};
    mi.cbSize = sizeof(mi);
    if (!GetMonitorInfoW(mon, &mi)) return;
    const RECT& wa = mi.rcWork;
    if (x + w > wa.right)  x = wa.right - w - 4;
    if (y + h > wa.bottom) y = wa.bottom - h - 4;
    if (x < wa.left)   x = wa.left + 4;
    if (y < wa.top)    y = wa.top + 4;
}

void Toggle() {
    g_visible = !g_visible;
    if (g_visible) {
        ShowWindow(g_panel, SW_SHOWNOACTIVATE);
    } else {
        ShowWindow(g_panel, SW_HIDE);
    }
    if (g_btn) InvalidateRect(g_btn, nullptr, TRUE);
    Log("[ui] 面板 %s", g_visible ? "开" : "关");
}

void CreateWindows() {
    HINSTANCE hi = GetModuleHandleW(nullptr);

    WNDCLASSEXW wc = {};
    wc.cbSize        = sizeof(wc);
    wc.lpfnWndProc   = DefWindowProcW;
    wc.hInstance     = hi;
    wc.hCursor       = LoadCursorW(nullptr, MAKEINTRESOURCEW(32512));  // IDC_ARROW
    wc.lpszClassName = kPanelClass;
    RegisterClassExW(&wc);

    g_panel = CreateWindowExW(
        WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST |
        WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        kPanelClass, L"", WS_POPUP, 0, 0, 10, 10,
        nullptr, nullptr, hi, nullptr);

    wc.lpfnWndProc   = BtnProc;
    wc.lpszClassName = kBtnClass;
    wc.hbrBackground = nullptr;
    RegisterClassExW(&wc);

    g_btn = CreateWindowExW(
        WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        kBtnClass, L"", WS_POPUP, 0, 0, kBtnW, kBtnH,
        nullptr, nullptr, hi, nullptr);

    g_font = CreateFontW(-14, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET,
                         OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                         ANTIALIASED_QUALITY, DEFAULT_PITCH, L"Microsoft YaHei");
    g_font_head = CreateFontW(-17, 0, 0, 0, FW_BOLD, 0, 0, 0, DEFAULT_CHARSET,
                              OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
                              ANTIALIASED_QUALITY, DEFAULT_PITCH,
                              L"Microsoft YaHei");
    Log("[ui] 面板窗口已创建 panel=%p btn=%p", (void*)g_panel, (void*)g_btn);
}

DWORD WINAPI UiThread(LPVOID) {
    // 等游戏把主窗口建出来（主菜单出现之前就有）
    for (int i = 0; i < 300 && !g_game; ++i) {
        g_game = FindGameWindow();
        if (!g_game) Sleep(200);
    }
    if (!g_game) {
        Log("[ui] 找不到游戏窗口，面板不启动");
        return 0;
    }
    CreateWindows();

    bool f1_down = false;
    for (;;) {
        // ★ 必须泵消息：不泵的话 WM_PAINT 永远不会被派发，按钮就是一块黑板；
        //   WM_LBUTTONUP 也收不到，点了没反应。我第一版就是只 Sleep 不泵，
        //   结果按钮窗口在、位置也对，就是全黑。
        MSG msg;
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        Sleep(40);

        // 位置同步 / 重绘按 200ms 一次就够，没必要每 40ms 折腾一遍
        static int tick = 0;
        if (++tick % 5) continue;

        if (!IsWindow(g_game)) {
            g_game = FindGameWindow();
            if (!g_game) continue;
        }

        // 游戏最小化 / 不在前台就把按钮藏起来，别飘在别人窗口上
        bool show_btn = IsWindowVisible(g_game) && !IsIconic(g_game);

        RECT cr;
        GetClientRect(g_game, &cr);
        POINT origin{ 0, 0 };
        ClientToScreen(g_game, &origin);
        int cw = cr.right - cr.left;
        int ch = cr.bottom - cr.top;

        if (show_btn && cw > 200 && ch > 200) {
            int bx = origin.x + cw - kBtnW - 8;
            int by = origin.y + ch - kBtnH - 8;
            ClampToWorkArea(g_game, bx, by, kBtnW, kBtnH);
            SetWindowPos(g_btn, HWND_TOPMOST, bx, by, kBtnW, kBtnH,
                         SWP_NOACTIVATE | SWP_SHOWWINDOW);

            if (g_visible) {
                int px = origin.x + cw - 470 - 8;
                int py = origin.y + 12;
                ClampToWorkArea(g_game, px, py, 470, 300);
                RenderPanel(g_panel, px, py, cw, ch);
                SetWindowPos(g_panel, HWND_TOPMOST, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW);
            }
        } else {
            ShowWindow(g_btn, SW_HIDE);
        }

        // F1 也能开关（游戏吃掉焦点时按钮点不到，留个键盘后门）
        bool down = (GetAsyncKeyState(VK_F1) & 0x8000) != 0;
        if (down && !f1_down) Toggle();
        f1_down = down;
    }
    return 0;
}

}  // namespace

void OverlayStart() {
    HANDLE t = CreateThread(nullptr, 0, UiThread, nullptr, 0, nullptr);
    if (t) CloseHandle(t);
}

}  // namespace hx
