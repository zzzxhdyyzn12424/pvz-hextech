// ============================================================================
//  proxy.cpp —— wsock32.dll 代理 + 海克斯线程入口
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

#include "log.h"
#include "pvz.h"

namespace {

// 里程碑 1 的目标只有一个：证明代码真的在游戏进程里跑起来了。
// 做法是每秒抓一次场景快照，只在「有变化」时写日志，避免刷屏。
DWORD WINAPI ProbeThread(LPVOID) {
    hx::LogReset();
    hx::Log("=== hextech in-process (milestone 1) ===");
    hx::Log("module base = %p", (void*)GetModuleHandleA("wsock32.dll"));

    // 等游戏把 gLawnApp 建起来（主菜单之前就 non-null，通常几秒内）
    for (int i = 0; i < 600; ++i) {
        if (hx::Plausible(hx::Rd<uint32_t>(hx::kGLawnApp))) break;
        Sleep(100);
    }

    hx::Snapshot prev;
    int idle = 0;
    for (int tick = 0; tick < 3600; ++tick) {     // 最多观察 1 小时
        hx::Snapshot s = hx::Grab();

        bool changed = !prev.valid
                       || s.board != prev.board
                       || s.sun != prev.sun
                       || s.wave != prev.wave
                       || s.plants != prev.plants
                       || s.zombies != prev.zombies;

        if (changed) {
            hx::Log("[t=%4d] app=%p board=%p scene=%d level=%d wave=%d/%d "
                    "sun=%d plants=%d zombies=%d",
                    tick, (void*)s.app, (void*)s.board, s.scene, s.level,
                    s.wave, s.wave_total, s.sun, s.plants, s.zombies);
            prev = s;
            idle = 0;
        } else if (++idle == 30) {
            hx::Log("[t=%4d] ...无变化（还在跑）", tick);
            idle = 0;
        }

        Sleep(1000);
    }
    hx::Log("[x] 观察结束");
    return 0;
}

}  // namespace

BOOL WINAPI DllMain(HMODULE hModule, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hModule);
        HANDLE t = CreateThread(nullptr, 0, ProbeThread, nullptr, 0, nullptr);
        if (t) CloseHandle(t);
    }
    return TRUE;
}
