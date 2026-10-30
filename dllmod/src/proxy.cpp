// ============================================================================
//  proxy.cpp —— wsock32.dll 代理 + 海克斯引擎入口
//
//  为什么是 wsock32：
//    PVZ 1.0.0.1051 的导入表里只有 KERNEL32 / USER32 / WININET / WINMM /
//    WSOCK32 / GDI42 / ADVAPI32 / SHELL32 / ole32 / OLEAUT32。
//    其中 wsock32 导出最少（75 条，全部具名，没有仅序号的），
//    而且不在 KnownDLLs 里 —— 所以游戏目录里放一个同名 dll 能抢在系统目录前面。
//
//  驱动方式（M4）：
//    主驱动 = 内联钩子挂在 Board::Update 里的 0x539D30，每帧一次，跑在游戏主线程。
//    辅助线程只负责低频写日志（每 2 秒一次），不参与逻辑。
//    万一挂钩失败，自动退回「100ms 轮询」模式，功能打折但不会瞎。
// ============================================================================
#include <windows.h>
#include <cstdio>

#include "engine.h"
#include "hook.h"
#include "log.h"
#include "pvz.h"

namespace {

// 低频状态日志线程（不参与词条逻辑，纯给人看）
DWORD WINAPI StatThread(LPVOID) {
    Sleep(500);
    hx::LogReset();
    hx::Log("=== hextech in-process (M4) ===");
    hx::Log("module base = %p", (void*)GetModuleHandleA("wsock32.dll"));

    for (int i = 0; i < 600; ++i) {
        if (hx::Plausible(hx::Rd<uint32_t>(hx::kGLawnApp))) break;
        Sleep(100);
    }

    bool hooked = hx::InstallFrameHook();
    hx::GetEngine().Init((unsigned)GetTickCount());
    if (!hooked) {
        hx::Log("[!] 挂钩失败，退回 100ms 轮询（每帧重算类词条会不准）");
    }

    hx::Snapshot prev;
    for (int i = 0; i < 7200; ++i) {          // 约 4 小时
        if (!hooked) hx::GetEngine().Tick();  // 兜底驱动

        hx::Snapshot s = hx::Grab();
        bool changed = !prev.valid
                       || s.board != prev.board
                       || s.sun != prev.sun
                       || s.plants != prev.plants
                       || s.zombies != prev.zombies;
        if (changed) {
            hx::Log("[stat] board=%p sun=%d plants=%d zombies=%d", (void*)s.board,
                    s.sun, s.plants, s.zombies);
            prev = s;
        }
        Sleep(2000);
    }
    return 0;
}

}  // namespace

BOOL WINAPI DllMain(HMODULE hModule, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hModule);
        HANDLE t = CreateThread(nullptr, 0, StatThread, nullptr, 0, nullptr);
        if (t) CloseHandle(t);
    }
    return TRUE;
}
