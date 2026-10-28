// ============================================================================
//  hook.h / 内联实现  ——  x86 5 字节 jmp 钩子 + 跳板(trampoline)
//
//  为什么需要跳板：
//    0x00520AC0 的入口是
//        56           push esi
//        8B F0        mov esi, eax
//        8B 07        mov eax, [edi]
//        8B 4E 34     mov ecx, [esi+0x34]
//    凑够 5 字节会正好切在 `8B 4E 34` 中间（4+3=7，够但仍要按指令边界）。
//    直接把函数头 5 字节换成 E9 会破坏这两条被覆盖的指令，
//    所以必须：把被覆盖的完整指令抄到别处，执行完再跳回原函数后续。
//
//  跳板内存：
//    用 VirtualAlloc 在 ±2GB 范围内分配（E9 是 rel32，超出就跳不到），
//    先试模块附近，失败再退回任意位置（那时改用 FF 25 绝对跳）。
// ============================================================================

#pragma once
#include <windows.h>
#include <cstdint>
#include <cstring>
#include <vector>

namespace Hook {

// 一条指令的粗略长度（只需覆盖 PVZ 里实际出现的前几条）
// 返回 0 表示不认识 —— 调用方应放弃挂钩而不是硬切。
inline int InsnLen(const uint8_t* p) {
    uint8_t op = p[0];

    // 单字节
    if (op == 0x50 || op == 0x51 || op == 0x52 || op == 0x53 ||
        op == 0x56 || op == 0x57 || op == 0x58 || op == 0x5E ||
        op == 0x5F || op == 0x55 || op == 0x5D || op == 0x90 ||
        op == 0xCC || op == 0x98 || op == 0x99) return 1;

    if (op == 0xC3) return 1;                       // ret

    // push imm32 / push imm8
    if (op == 0x68) return 5;
    if (op == 0x6A) return 2;

    // mov r32, imm32
    if (op == 0xB8 || op == 0xB9 || op == 0xBA || op == 0xBB ||
        op == 0xBC || op == 0xBD || op == 0xBE || op == 0xBF) return 5;

    // mov eax, [abs32] / mov [abs32], eax  (A1 / A3)
    if (op == 0xA1 || op == 0xA3) return 5;

    // jmp/call rel32
    if (op == 0xE9 || op == 0xE8) return 5;
    // jcc rel8
    if (op == 0x70 || op == 0x71 || op == 0x72 || op == 0x73 ||
        op == 0x74 || op == 0x75 || op == 0x76 || op == 0x77 ||
        op == 0x78 || op == 0x79 || op == 0x7A || op == 0x7B ||
        op == 0x7C || op == 0x7D || op == 0x7E || op == 0x7F) return 2;
    // jmp rel8
    if (op == 0xEB) return 2;

    // test r/m32, r32 —— 85 /r
    // mov r, r/m32       —— 8B /r
    // lea r, m           —— 8D /r
    // add/cmp/and/or/xor/sub —— 83 /r ib 或 03/2B/39/3B /r
    if (op == 0x85 || op == 0x8B || op == 0x89 || op == 0x8D ||
        op == 0x03 || op == 0x2B || op == 0x39 || op == 0x3B ||
        op == 0x01 || op == 0x29 || op == 0x31 || op == 0x33) {
        uint8_t modrm = p[1];
        uint8_t mod = modrm >> 6;
        uint8_t rm  = modrm & 7;
        if (mod == 3) return 2;
        int len = 2;
        if (rm == 4) len += 1;                       // SIB
        if (mod == 1) len += 1;
        else if (mod == 2) len += 4;
        else if (mod == 0 && rm == 5) len += 4;       // disp32
        return len;
    }

    // 83 /r ib
    if (op == 0x83) {
        uint8_t modrm = p[1];
        uint8_t mod = modrm >> 6;
        uint8_t rm  = modrm & 7;
        int len = 2;
        if (mod != 3) {
            if (rm == 4) len += 1;
            if (mod == 1) len += 1;
            else if (mod == 2) len += 4;
            else if (mod == 0 && rm == 5) len += 4;
        }
        return len + 1;
    }

    // 81 /r id
    if (op == 0x81) {
        uint8_t modrm = p[1];
        uint8_t mod = modrm >> 6;
        uint8_t rm  = modrm & 7;
        int len = 2;
        if (mod != 3) {
            if (rm == 4) len += 1;
            if (mod == 1) len += 1;
            else if (mod == 2) len += 4;
            else if (mod == 0 && rm == 5) len += 4;
        }
        return len + 4;
    }

    // 64 A1 xx xx xx xx  (fs:)
    if (op == 0x64) return 6;

    // x87: D8..DF /r
    if (op >= 0xD8 && op <= 0xDF) {
        uint8_t modrm = p[1];
        uint8_t mod = modrm >> 6;
        uint8_t rm  = modrm & 7;
        if (mod == 3) return 2;
        int len = 2;
        if (rm == 4) len += 1;
        if (mod == 1) len += 1;
        else if (mod == 2) len += 4;
        else if (mod == 0 && rm == 5) len += 4;
        return len;
    }

    // 0F xx
    if (op == 0x0F) {
        uint8_t op2 = p[1];
        if (op2 >= 0x80 && op2 <= 0x8F) return 6;    // jcc rel32
        if (op2 == 0xB6 || op2 == 0xB7 || op2 == 0xBE || op2 == 0xBF) {
            uint8_t modrm = p[2];
            uint8_t mod = modrm >> 6;
            uint8_t rm  = modrm & 7;
            if (mod == 3) return 3;
            int len = 3;
            if (rm == 4) len += 1;
            if (mod == 1) len += 1;
            else if (mod == 2) len += 4;
            else if (mod == 0 && rm == 5) len += 4;
            return len;
        }
        // 0F 85 之类已覆盖；其余按 modrm 走
        uint8_t modrm = p[2];
        uint8_t mod = modrm >> 6;
        uint8_t rm  = modrm & 7;
        if (mod == 3) return 3;
        int len = 3;
        if (rm == 4) len += 1;
        if (mod == 1) len += 1;
        else if (mod == 2) len += 4;
        else if (mod == 0 && rm == 5) len += 4;
        return len;
    }

    return 0;   // 不认识
}

struct Instance {
    uint8_t* target  = nullptr;
    uint8_t* tramp   = nullptr;
    size_t   copyLen = 0;
    uint8_t  saved[16] = {0};
    bool     active  = false;

    // 在模块附近分配可执行内存（rel32 可达）
    static uint8_t* AllocNear(uint8_t* hint, size_t size) {
        SYSTEM_INFO si{};
        GetSystemInfo(&si);
        uintptr_t gran = si.dwAllocationGranularity;
        uintptr_t want = reinterpret_cast<uintptr_t>(hint);

        for (int i = 1; i <= 512; ++i) {
            for (int sign = -1; sign <= 1; sign += 2) {
                uintptr_t cand = want + static_cast<uintptr_t>(sign) *
                                        static_cast<uintptr_t>(i) * gran * 8;
                void* p = VirtualAlloc(reinterpret_cast<void*>(cand), size,
                                       MEM_COMMIT | MEM_RESERVE,
                                       PAGE_EXECUTE_READWRITE);
                if (p) return static_cast<uint8_t*>(p);
            }
        }
        return static_cast<uint8_t*>(
            VirtualAlloc(nullptr, size, MEM_COMMIT | MEM_RESERVE,
                         PAGE_EXECUTE_READWRITE));
    }

    // det: 我们的钩子函数地址
    bool Install(void* tgt, const void* det) {
        if (!tgt || !det || active) return false;
        target = static_cast<uint8_t*>(tgt);

        // 1) 算出要覆盖多少字节（>=5，且落在指令边界）
        size_t need = 0;
        while (need < 5) {
            int len = InsnLen(target + need);
            if (len <= 0) return false;
            need += static_cast<size_t>(len);
        }
        if (need > sizeof(saved)) return false;
        copyLen = need;

        // 2) 分配跳板：copyLen 字节原指令 + 5 字节 jmp 回去
        tramp = AllocNear(target, 64);
        if (!tramp) return false;

        DWORD old = 0;
        if (!VirtualProtect(target, copyLen, PAGE_EXECUTE_READWRITE, &old))
            return false;
        memcpy(saved, target, copyLen);
        memcpy(tramp, target, copyLen);

        // 3) 跳板尾部：E9 回原函数 copyLen 之后
        uint8_t* back = tramp + copyLen;
        intptr_t backRel = reinterpret_cast<intptr_t>(target + copyLen) -
                           (reinterpret_cast<intptr_t>(back) + 5);
        if (backRel < INT32_MIN || backRel > INT32_MAX) {
            VirtualFree(tramp, 0, MEM_RELEASE);
            tramp = nullptr;
            return false;
        }
        back[0] = 0xE9;
        int32_t br = static_cast<int32_t>(backRel);
        memcpy(back + 1, &br, 4);

        // 4) 原函数头：E9 到 det
        intptr_t rel = reinterpret_cast<intptr_t>(det) -
                       (reinterpret_cast<intptr_t>(target) + 5);
        if (rel < INT32_MIN || rel > INT32_MAX) {
            VirtualFree(tramp, 0, MEM_RELEASE);
            tramp = nullptr;
            return false;
        }
        // 先把多余字节填 NOP，再写 E9
        memset(target, 0x90, copyLen);
        target[0] = 0xE9;
        int32_t r32 = static_cast<int32_t>(rel);
        memcpy(target + 1, &r32, 4);

        VirtualProtect(target, copyLen, old, &old);
        FlushInstructionCache(GetCurrentProcess(), target, copyLen);
        FlushInstructionCache(GetCurrentProcess(), tramp, copyLen + 5);

        active = true;
        return true;
    }

    void Remove() {
        if (!active || !target) return;
        DWORD old = 0;
        VirtualProtect(target, copyLen, PAGE_EXECUTE_READWRITE, &old);
        memcpy(target, saved, copyLen);
        VirtualProtect(target, copyLen, old, &old);
        FlushInstructionCache(GetCurrentProcess(), target, copyLen);
        if (tramp) { VirtualFree(tramp, 0, MEM_RELEASE); tramp = nullptr; }
        active = false;
        target = nullptr;
    }
};

} // namespace Hook
