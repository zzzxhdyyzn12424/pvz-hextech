// ============================================================================
//  hook.cpp —— 装 0x539D30 的 jmp 钩子
//
//  原指令：E9 <rel32>   (jmp 0x537F50)
//  改成：  E9 <rel32>   (jmp _hx_detour)
//  只有 5 个字节，不需要搬运任何指令，也就没有「指令长度截断」这类经典事故。
// ============================================================================
#include "hook.h"

#include <windows.h>

#include "log.h"

// 由 detour.S 提供
extern "C" void hx_detour();

// detour 跳回原函数体时读这个变量
extern "C" void* hx_real_body = nullptr;

namespace hx {

bool InstallFrameHook() {
    hx_real_body = (void*)kFrameBody;

    unsigned char* p = (unsigned char*)kFrameThunk;

    // 先确认这里确实是一条 jmp rel32，别在别的版本上乱改
    if (p[0] != 0xE9) {
        Log("[hook] 0x%p 不是 E9（读到 %02X），版本不对，放弃挂钩",
            (void*)kFrameThunk, p[0]);
        return false;
    }
    int old_rel = *(int*)(p + 1);
    uintptr_t old_dst = kFrameThunk + 5 + old_rel;
    Log("[hook] 原 jmp 目标 = 0x%p（预期 0x%p）", (void*)old_dst,
        (void*)kFrameBody);

    DWORD old_prot = 0;
    if (!VirtualProtect(p, 5, PAGE_EXECUTE_READWRITE, &old_prot)) {
        Log("[hook] VirtualProtect 失败 err=%lu", GetLastError());
        return false;
    }

    int new_rel = (int)((uintptr_t)&hx_detour - (kFrameThunk + 5));
    p[0] = 0xE9;
    *(int*)(p + 1) = new_rel;

    DWORD tmp = 0;
    VirtualProtect(p, 5, old_prot, &tmp);
    FlushInstructionCache(GetCurrentProcess(), p, 5);

    Log("[hook] 已挂钩 0x%p -> detour 0x%p（rel=%d）", (void*)kFrameThunk,
        (void*)&hx_detour, new_rel);
    return true;
}

}  // namespace hx
