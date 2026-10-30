// ============================================================================
//  proxy.cpp —— wsock32.dll 代理 + 海克斯引擎入口
//
//  为什么是 wsock32：
//    PVZ 1.0.0.1051 的导入表里只有 KERNEL32 / USER32 / WININET / WINMM /
//    WSOCK32 / GDI42 / ADVAPI32 / SHELL32 / ole32 / OLEAUT32。
//    其中 wsock32 导出最少（75 条，全部具名，没有仅序号的），
//    而且不在 KnownDLLs 里 —— 所以游戏目录里放一个同名 dll 能抢在系统目录前面。
//
//  导出转发在 gen/proxy.def 里生成，本文件只管「加载后干什么」。
// ============================================================================
#include <windows.h>
#include <cstdio>

#include "engine.h"
#include "log.h"
#include "pvz.h"

namespace {

// 主循环：每 100ms 跑一次引擎（游戏是 100 Update/秒，M4 再改成挂进 Update）
DWORD WINAPI HexThread(LPVOID) {
    // 先睡一下再干活：DllMain 是在加载器锁里跑的，新线程如果立刻调 CRT/文件 API，
    // 可能撞上还没初始化完的状态。500ms 足够加载器收尾。
    Sleep(500);

    hx::LogReset();
    hx::Log("=== hextech in-process (M3) ===");
    hx::Log("module base = %p", (void*)GetModuleHandleA("wsock32.dll"));

    // 等 gLawnApp 建起来
    for (int i = 0; i < 600; ++i) {
        if (hx::Plausible(hx::Rd<uint32_t>(hx::kGLawnApp))) break;
        Sleep(100);
    }

    hx::GetEngine().Init((unsigned)GetTickCount());

    hx::Snapshot prev;
    for (int tick = 0; tick < 36000; ++tick) {
        hx::GetEngine().Tick();

        hx::Snapshot s = hx::Grab();
        bool changed = !prev.valid
                       || s.board != prev.board
                       || s.sun != prev.sun
                       || s.plants != prev.plants
                       || s.zombies != prev.zombies;
        if (changed) {
            hx::Log("[t=%5d] board=%p sun=%d plants=%d zombies=%d", tick,
                    (void*)s.board, s.sun, s.plants, s.zombies);
            prev = s;
        }
        Sleep(100);
    }
    hx::Log("[x] 主循环结束");
    return 0;
}

}  // namespace

BOOL WINAPI DllMain(HMODULE hModule, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hModule);
        HANDLE t = CreateThread(nullptr, 0, HexThread, nullptr, 0, nullptr);
        if (t) CloseHandle(t);
    }
    return TRUE;
}
