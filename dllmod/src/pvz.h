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
constexpr uint32_t B_WAVE       = 0x5568;
constexpr uint32_t B_SEEDBANK   = 0x144;

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
// 进程内解引用最怕空指针和野指针。游戏对象都是堆上的，
// 地址一定落在用户态低 2G 且 4 字节对齐，这里做个下限检查挡掉明显垃圾。
inline bool Plausible(uintptr_t p) {
    return p >= 0x10000 && p < 0x7FFF0000;
}

template <typename T>
inline T Rd(uintptr_t p) {
    if (!Plausible(p)) return T(0);
    return *reinterpret_cast<T*>(p);
}

template <typename T>
inline bool Wr(uintptr_t p, T v) {
    if (!Plausible(p)) return false;
    *reinterpret_cast<T*>(p) = v;
    return true;
}

// 安全读一个指针字段（返回 0 表示读不到）
inline uintptr_t Ptr(uintptr_t base, uint32_t off) {
    if (!Plausible(base)) return 0;
    uintptr_t v = Rd<uint32_t>(base + off);
    return Plausible(v) ? v : 0;
}

// ---- 场景快照 ----
struct Snapshot {
    uintptr_t app = 0;
    uintptr_t board = 0;
    int sun = 0;
    int scene = 0;
    int level = 0;
    int wave = 0;
    int wave_total = 0;
    int plants = 0;       // 存活植物数
    int zombies = 0;      // 存活僵尸数
    bool valid = false;
};

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
    s.wave       = Rd<int>(s.board + B_WAVE);
    s.wave_total = Rd<int>(s.board + B_WAVE_TOTAL);

    // 植物数组：Plant** + 上限
    uintptr_t parr = Ptr(s.board, B_PLANT);
    int pmax = Rd<int>(s.board + B_PLANT_MAX);
    if (parr && pmax > 0 && pmax < 1024) {
        for (int i = 0; i < pmax; ++i) {
            uintptr_t p = Rd<uint32_t>(parr + i * 4);
            if (!Plausible(p)) continue;
            if (Rd<uint8_t>(p + P_DEAD)) continue;
            s.plants++;
        }
    }

    // 僵尸数组
    uintptr_t zarr = Ptr(s.board, B_ZOMBIE);
    int zmax = Rd<int>(s.board + B_ZOMBIE_MAX);
    if (zarr && zmax > 0 && zmax < 4096) {
        for (int i = 0; i < zmax; ++i) {
            uintptr_t z = Rd<uint32_t>(zarr + i * 4);
            if (!Plausible(z)) continue;
            if (Rd<uint8_t>(z + Z_DEAD)) continue;
            s.zombies++;
        }
    }

    s.valid = true;
    return s;
}

}  // namespace hx
