// ============================================================================
//  pvz.h —— 游戏内存布局 + 进程内访问器
//
//  和外置版（tools/hextech_core.py）的区别：
//    外置要 OpenProcess + ReadProcessMemory，每次读都过一次内核；
//    这里我们就在游戏进程里，直接解引用指针，没有系统调用开销。
//
//  所有偏移都来自 tools/hextech_core.py 的实测值（1.0.0.1051 中文版），
//  改游戏版本必须重新校准。
// ============================================================================
#pragma once

#include <windows.h>
#include <cstdint>

namespace hx {

// ---- 全局对象 ----
constexpr uintptr_t kGLawnApp = 0x006A9EC0;
constexpr uint32_t kOffBoard = 0x768;

// ---- Board 字段 ----
constexpr uint32_t B_ZOMBIE     = 0x090;   // Zombie** 数组
constexpr uint32_t B_ZOMBIE_MAX = 0x094;
constexpr uint32_t B_PLANT      = 0x0AC;   // Plant** 数组
constexpr uint32_t B_PLANT_MAX  = 0x0B0;
constexpr uint32_t B_SCENE      = 0x554C;
constexpr uint32_t B_LEVEL      = 0x5550;
constexpr uint32_t B_SUN        = 0x5560;
constexpr uint32_t B_WAVE_TOTAL = 0x5564;
constexpr uint32_t B_TICK       = 0x5568;   // 实测是「每秒 +100」的帧/计时器，不是波数
constexpr uint32_t B_SEEDBANK   = 0x144;

// ---- 对象数组 ----
// 重要：这两个数组是**按值内联的对象数组**，不是指针数组！
//   Board+0xAC = Plant 对象数组的首地址，第 i 株在 base + i*0x14C
//   Board+0xB0 = 已用槽位数
// 每个对象开头是 GameObject 指纹：+0x00 == gLawnApp、+0x04 == Board。
// （一开始我按「指针数组」写，结果 plants 永远是 0，白折腾一轮。）
constexpr uint32_t PLANT_STRIDE  = 0x14C;
constexpr uint32_t ZOMBIE_STRIDE = 0x15C;
constexpr int MAX_PLANTS  = 512;
constexpr int MAX_ZOMBIES = 1024;

// ---- GameObject 通用 ----
constexpr uint32_t O_X = 0x08;
constexpr uint32_t O_Y = 0x0C;
constexpr uint32_t O_W = 0x10;
constexpr uint32_t O_H = 0x14;

// ---- Plant ----
constexpr uint32_t P_ROW      = 0x1C;
constexpr uint32_t P_TYPE     = 0x24;
constexpr uint32_t P_COL      = 0x28;
constexpr uint32_t P_HP       = 0x40;
constexpr uint32_t P_HP_MAX   = 0x44;
constexpr uint32_t P_DEAD     = 0x141;
constexpr uint32_t P_SHOOT_CNT = 0x58;
constexpr uint32_t P_SHOOT_INT = 0x5C;

// ---- Zombie ----
constexpr uint32_t Z_TYPE   = 0x24;
constexpr uint32_t Z_STATUS = 0x28;
constexpr uint32_t Z_HP     = 0xC8;
constexpr uint32_t Z_HELMET = 0xD0;
constexpr uint32_t Z_SHIELD_TYPE = 0xD8;
constexpr uint32_t Z_SHIELD = 0xDC;
constexpr uint32_t Z_DEAD   = 0xEC;

// ---- 安全访问 ----
// 进程内解引用最怕野指针。只做「地址范围」检查是不够的：
// 一个垃圾指针只要落在 0x10000..0x7FFF0000 里就会过检，然后解引用直接崩游戏。
// 必须问操作系统「这一页到底是不是可读的已提交内存」——VirtualQuery 干这个。
//
// 外置版没这个问题：ReadProcessMemory 读不到就返回失败，天然安全。
// 进程内没有这层保护，得自己补上。**这是进程内方案最容易踩的坑。**
inline bool Plausible(uintptr_t p) {
    return p >= 0x10000 && p < 0x7FFF0000;
}

inline bool Readable(uintptr_t p, size_t n) {
    if (!Plausible(p)) return false;
    if (p + n < p) return false;                    // 溢出
    MEMORY_BASIC_INFORMATION mbi;
    if (VirtualQuery((LPCVOID)p, &mbi, sizeof(mbi)) != sizeof(mbi)) return false;
    if (mbi.State != MEM_COMMIT) return false;
    if (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)) return false;
    uintptr_t region_end = (uintptr_t)mbi.BaseAddress + mbi.RegionSize;
    return (p + n) <= region_end;
}

template <typename T>
inline T Rd(uintptr_t p) {
    if (!Readable(p, sizeof(T))) return T(0);
    return *reinterpret_cast<T*>(p);
}

template <typename T>
inline bool Wr(uintptr_t p, T v) {
    if (!Readable(p, sizeof(T))) return false;
    *reinterpret_cast<T*>(p) = v;
    return true;
}

// 安全读一个指针字段（返回 0 表示读不到）
inline uintptr_t Ptr(uintptr_t base, uint32_t off) {
    uintptr_t v = Rd<uint32_t>(base + off);
    return Plausible(v) ? v : 0;
}


// 不做 VirtualQuery 的快速读：只用于**已经确认落在同一块已提交内存里**的地址。
// 对象数组是游戏一次性分配的连续内存，槽位永远不会被释放/搬迁，
// 所以只要校验过一次数组基址，槽内字段就可以直接解引用。
// （每帧 100 次 × 几十个字段，全走 VirtualQuery 的话每秒上万次系统调用，太慢。）
template <typename T>
inline T RdFast(uintptr_t p) {
    return *reinterpret_cast<T*>(p);
}

// 一个数组最多能安全读多少个槽（受所在内存区域大小限制）
inline int SpanSlots(uintptr_t base, uint32_t stride, int want) {
    MEMORY_BASIC_INFORMATION mbi;
    if (VirtualQuery((LPCVOID)base, &mbi, sizeof(mbi)) != sizeof(mbi)) return 0;
    if (mbi.State != MEM_COMMIT) return 0;
    if (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)) return 0;
    uintptr_t end = (uintptr_t)mbi.BaseAddress + mbi.RegionSize;
    if (end <= base) return 0;
    size_t bytes = (size_t)(end - base);
    int n = (int)(bytes / stride);
    return n < want ? n : want;
}

// ---- 场景快照 ----
struct Snapshot {
    uintptr_t app = 0;
    uintptr_t board = 0;
    int sun = 0;
    int scene = 0;
    int level = 0;
    int tick = 0;
    int wave_total = 0;
    int plants = 0;       // 存活植物数
    int zombies = 0;      // 存活僵尸数
    bool valid = false;
};

// 数一遍某个内联对象数组里的存活个体
inline int CountAlive(uintptr_t board, uintptr_t app, uint32_t arr_off,
                      uint32_t cnt_off, uint32_t stride, uint32_t dead_off,
                      uint32_t hp_off, int hard_max) {
    uintptr_t base = Ptr(board, arr_off);
    int cap = Rd<int>(board + cnt_off);
    if (!base || cap <= 0) return 0;
    if (cap > hard_max) cap = hard_max;
    int n = 0;
    for (int i = 0; i < cap; ++i) {
        uintptr_t o = base + (uintptr_t)i * stride;
        if (Rd<uint32_t>(o) != (uint32_t)app) continue;        // 指纹
        if (Rd<uint32_t>(o + 4) != (uint32_t)board) continue;  // 指纹
        if (Rd<uint8_t>(o + dead_off)) continue;
        if (Rd<int>(o + hp_off) <= 0) continue;
        n++;
    }
    return n;
}

inline Snapshot Grab() {
    Snapshot s;
    s.app = Rd<uint32_t>(kGLawnApp);
    if (!Plausible(s.app)) return s;
    s.board = Ptr(s.app, kOffBoard);
    // 注意：主菜单里 board 还是 0，这不算失败。valid 只表示「app 拿到了」，
    // 否则日志会把同一行重复刷屏（第一次踩过这个坑）。
    s.valid = true;
    if (!s.board) return s;

    s.sun        = Rd<int>(s.board + B_SUN);
    s.scene      = Rd<int>(s.board + B_SCENE);
    s.level      = Rd<int>(s.board + B_LEVEL);
    s.tick       = Rd<int>(s.board + B_TICK);
    s.wave_total = Rd<int>(s.board + B_WAVE_TOTAL);

    s.plants = CountAlive(s.board, s.app, B_PLANT, B_PLANT_MAX,
                          PLANT_STRIDE, P_DEAD, P_HP, MAX_PLANTS);
    s.zombies = CountAlive(s.board, s.app, B_ZOMBIE, B_ZOMBIE_MAX,
                           ZOMBIE_STRIDE, Z_DEAD, Z_HP, MAX_ZOMBIES);
    return s;
}

}  // namespace hx
