// ============================================================================
//  overlay_test.cpp —— 离线渲染自检（不进游戏）
//
//  把 overlay.cpp 整份 include 进来，就能直接调它匿名命名空间里的
//  DrawHp / DrawCards / DrawPanel。喂一份假快照，画到内存 DC，存成 BMP。
//
//  这样能在不开游戏的情况下核对：
//    · 血条位置/层数/颜色对不对
//    · 词条小方块的字有没有居中、有没有超出格子
//    · 面板里的中文有没有乱码（%ls 那个坑）
//
//  编译：见 build_overlay_test.bat
// ============================================================================
#include <windows.h>

#include <cstdio>
#include <cstring>

#include "src/overlay.cpp"

namespace hx {
// 只给链接用：这些在测试里不会被真正调用
void Engine::CopyOverlay(OverlaySnap&) const {}
Engine& GetEngine() {
    static Engine e;
    return e;
}
}  // namespace hx

// ---- BMP 落盘 ----
static void SaveBmp(const char* path, HDC dc, int w, int h) {
    BITMAPINFOHEADER bi = {};
    bi.biSize = sizeof(bi);
    bi.biWidth = w;
    bi.biHeight = -h;
    bi.biPlanes = 1;
    bi.biBitCount = 24;
    bi.biCompression = BI_RGB;
    int stride = ((w * 3 + 3) / 4) * 4;
    std::vector<unsigned char> buf((size_t)stride * h);
    GetDIBits(dc, (HBITMAP)GetCurrentObject(dc, OBJ_BITMAP), 0, h, buf.data(),
              (BITMAPINFO*)&bi, DIB_RGB_COLORS);

    FILE* f = fopen(path, "wb");
    if (!f) return;
    unsigned char hdr[54] = {0};
    hdr[0] = 'B'; hdr[1] = 'M';
    int filesize = 54 + stride * h;
    memcpy(hdr + 2, &filesize, 4);
    int off = 54; memcpy(hdr + 10, &off, 4);
    int hs = 40; memcpy(hdr + 14, &hs, 4);
    memcpy(hdr + 18, &w, 4);
    int nh = -h; memcpy(hdr + 22, &nh, 4);
    short planes = 1, bpp = 24;
    memcpy(hdr + 26, &planes, 2);
    memcpy(hdr + 28, &bpp, 2);
    int imgsz = stride * h; memcpy(hdr + 34, &imgsz, 4);
    fwrite(hdr, 1, 54, f);
    fwrite(buf.data(), 1, buf.size(), f);
    fclose(f);
}

int main() {
    using namespace hx;

    const int W = 800, H = 600;
    HDC screen = GetDC(nullptr);
    HDC mem = CreateCompatibleDC(screen);
    HBITMAP bmp = CreateCompatibleBitmap(screen, W, H);
    SelectObject(mem, bmp);

    // 假装是草坪：绿色底 + 淡格子，方便看血条对比度
    auto paintBackground = [&]() {
        Fill(mem, 0, 0, W, H, RGB(0x3E, 0x6B, 0x2E));
        for (int r = 0; r < 5; ++r)
            for (int c = 0; c < 9; ++c)
                Fill(mem, 40 + c * 80, 80 + r * 100, 120 + c * 80, 180 + r * 100,
                     ((r + c) & 1) ? RGB(0x46, 0x76, 0x33) : RGB(0x40, 0x6E, 0x2F));
    };
    paintBackground();

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

    Engine::OverlaySnap s;
    auto plant = [&](int type, int row, int col, int hp, int mx, int h0, int h1) {
        Engine::OverlayObj o = {};
        o.plant = true; o.type = type; o.row = row; o.col = col;
        o.hp = hp; o.hp_max = mx;
        o.x = 40 + col * 80 + 20; o.y = 80 + row * 100 + 40;
        o.w = 60; o.h = 70;
        o.hex[0] = h0; o.hex[1] = h1; o.nhex = (h1 >= 0) ? 2 : 1;
        s.objs.push_back(o);
    };
    auto zomb = [&](int type, int row, int x, int hp, int mx, int sh, int shm,
                    int hl, int hlm, int h0) {
        Engine::OverlayObj o = {};
        o.plant = false; o.type = type; o.row = row;
        o.hp = hp; o.hp_max = mx;
        o.shield = sh; o.shield_max = shm;
        o.helmet = hl; o.helmet_max = hlm;
        o.x = x; o.y = 80 + row * 100 + 30; o.w = 60; o.h = 90;
        o.hex[0] = h0; o.nhex = 1;
        s.objs.push_back(o);
    };

    // 用真实的词条 ID（按名字查，避免硬编码数字错位）
    auto id = [](const char* name) { return HexIndex(name); };

    plant(0, 0, 0, 300, 300, id("p_swift"), id("p_power"));      // 豌豆射手
    plant(1, 0, 1, 300, 300, id("p_inspire"), -1);               // 向日葵
    plant(6, 1, 2, 120, 300, id("p_titan"), id("p_chill"));      // 坚果
    plant(3, 2, 3, 40, 300, id("p_mountain"), id("p_storm"));    // 寒冰射手
    // 僵尸摆在右半边，面板关掉时能完整看到三层血条
    zomb(0, 0, 600, 200, 200, 0, 0, 0, 0, id("z_swift"));        // 普通僵尸
    zomb(4, 1, 520, 120, 200, 80, 80, 0, 0, id("z_heavy"));      // 路障
    zomb(5, 2, 440, 1500, 1500, 0, 0, 500, 500, id("z_goliath"));// 铁桶
    zomb(8, 3, 360, 80, 200, 0, 0, 0, 0, id("z_flash"));         // 撑杆

    s.plants = 4; s.zombies = 4; s.sun = 275; s.level = 3;

    // 第一张：面板关，专看血条和词条卡
    g_show_panel = false;
    DrawHp(mem, s);
    DrawCards(mem, s);
    SaveBmp("C:\\pvztc\\overlay_test_hp.bmp", mem, W, H);

    // 第二张：面板开，看详情面板排版 / 中文
    paintBackground();
    DrawHp(mem, s);
    DrawCards(mem, s);
    g_show_panel = true;
    DrawPanel(mem, s, W, H);
    SaveBmp("C:\\pvztc\\overlay_test_panel.bmp", mem, W, H);

    DeleteObject(bmp);
    DeleteDC(mem);
    ReleaseDC(nullptr, screen);
    printf("ok\n");
    return 0;
}
