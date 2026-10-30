// ============================================================================
//  engine.h —— 词条引擎（进程内版）
//
//  对应外置版的 Engine（tools/hextech_core.py）。差别：
//    · 没有 ReadProcessMemory，全部直接解引用
//    · 没有 overlay / tkinter，UI 以后用游戏自己的绘制
//    · 每帧由游戏自己的 Board::Update 驱动（M4 已挂钩）
//    · 时间基准改成「帧计数」：游戏每秒 100 次 Update，比 time.time() 稳，
//      而且游戏暂停时 Update 不跑，帧钟跟着停 —— 正好是我们想要的语义
// ============================================================================
#pragma once

#include <algorithm>
#include <cstdint>
#include <map>
#include <mutex>
#include <random>
#include <set>
#include <string>
#include <vector>

#include "hex.h"
#include "hex_table.gen.h"   // HexId 枚举（自动生成）

namespace hx {

// 词条 id -> kHexDefs 下标。Init 时填一次，之后 O(1)。
extern int gHexIdx[HEX_COUNT];

// 两条词条是否互斥（同一对象不能同时拥有）
inline bool HexConflict(int a, int b) {
    for (int g = 0; g < kExclGroupCount; ++g) {
        bool ha = false, hb = false;
        for (int k = 0; k < kExclWidth; ++k) {
            int v = kExclGroups[g][k];
            if (v < 0) break;
            if (v == a) ha = true;
            if (v == b) hb = true;
        }
        if (ha && hb) return true;
    }
    return false;
}

// 抽卡（带互斥组过滤）。
// hex.h 里的 RollHexes 只管「从池子里不重复地抽」，它看不到互斥表
// （互斥表在自动生成的头里，而那个头又 include 了 hex.h，只能绕开）。
// 所以在这里再包一层：抽到和已选的冲突就换一条。
inline std::vector<int> RollHexesEx(Scope scope, int plant_type, std::mt19937& rng) {
    std::uniform_int_distribution<int> pick_count(kHexMin, kHexMax);
    int count = pick_count(rng);

    std::vector<int> pool = PoolFor(scope, plant_type, false);
    std::uniform_real_distribution<double> uni(0.0, 1.0);
    if (uni(rng) < kPrismaticChance) {
        std::vector<int> extra = PoolFor(scope, plant_type, true);
        pool.insert(pool.end(), extra.begin(), extra.end());
    }

    std::vector<int> out;
    for (int k = 0; k < count && !pool.empty(); ++k) {
        std::uniform_int_distribution<size_t> pick(0, pool.size() - 1);
        size_t i = pick(rng);
        int cand = pool[i];
        pool.erase(pool.begin() + (ptrdiff_t)i);
        bool bad = false;
        for (int picked : out) {
            if (HexConflict(picked, cand)) { bad = true; break; }
        }
        if (bad) { --k; continue; }
        out.push_back(cand);
    }
    return out;
}

// 一个对象（植物或僵尸）本帧的快照
struct Obj {
    uintptr_t addr = 0;
    int idx = 0;
    int type = 0;
    int hp = 0;
    int hp_max = 0;     // 植物读真实 +0x44；僵尸没有这个字段，用出生血量
    int row = 0;
    int col = 0;
    int x = 0;
    int y = 0;
};

// 每个对象挂的词条状态 + 每帧记账
//
// 「记账」这部分是和外置版对齐的关键：外置版靠 ReadProcessMemory 每帧采样，
// 进程内一样要采样，只是少了系统调用。下面这些字段全部是「上一帧的读数」，
// 用来在没有伤害钩子的情况下推断「这一帧发生了什么」。
struct ObjState {
    std::vector<int> hexes;          // 词条下标
    std::set<std::string> fired;     // 一次性效果是否已触发

    // ---- 血量记账 ----
    int hp_max = 1;                  // 出生时血量（僵尸没有 max 字段，就取它）
    int hp_prev = 0;                 // 上一帧血量，用来判断「这一帧掉血了」
    int hits = 0;                    // 累计挨打次数

    // ---- 移速补位移 ----
    float prev_fx = 0.0f;            // 上一帧补完后的 +0x2C
    bool  fx_ok = false;
    // 累计「游戏自己走了多少」/「我们补了多少」。
    // 这两个数的比值就是实际倍率：ratio = (own+add)/own 应当等于 1+bonus。
    // 这是唯一能证明「移速真的生效」的证据 —— 看 +0x34 字段是看不出来的。
    double sum_own = 0.0;
    double sum_add = 0.0;

    // ---- 攻速 ----
    int shoot_base = 0;              // 反推出的原版射击间隔
    int rage_hits = 0;               // 羊刀叠层
    bool logged_shoot = false;       // 只在第一次写 +0x5C 时打日志

    // ---- 体型 ----
    int base_w = 0, base_h = 0;

    // ---- 护盾（借用僵尸原生护盾槽 +0xD8/+0xDC/+0xE0）----
    int shield = 0;
    int shield_max = 0;

    // ---- 计时（真实毫秒，见 tuning.h 顶部为什么不用帧数）----
    uint32_t escape_ms = 0;          // 逃跑计划触发时刻
    uint32_t escape_tick_ms = 0;     // 护盾衰减的上次结算时刻
    uint32_t last_hit_ms = 0;        // 上次掉血时刻

    bool has(HexId h) const {
        int i = gHexIdx[h];
        return i >= 0 && std::find(hexes.begin(), hexes.end(), i) != hexes.end();
    }
};

class Engine {
public:
    void Init(unsigned seed);

    // 面板用的一行。字符串在 Tick 里（游戏线程）一次性拼好，
    // 面板线程只读快照，两边不共享状态。
    struct OverlayRow {
        bool plant = false;
        int idx = 0;
        int type = 0;
        int hp = 0;
        int hp_max = 0;
        int shield = 0;      // 原生护盾槽里的当前值（山脉龙魂 / 逃跑计划）
        std::string name;
        std::string hexes;
    };

    // 跨线程取面板数据（内部加锁）
    void CopyOverlay(std::vector<OverlayRow>& out, int& plants, int& zombies,
                     uint32_t& age_ms) const;

    // 每帧调用（游戏 Board::Update 开头）
    void Tick();

    int plant_count() const { return stats_plant_; }
    int zombie_count() const { return stats_zombie_; }
    uint32_t frame() const { return frame_; }

    // 本帧诊断（每帧清零，供低频日志采样）
    int    speed_objs() const { return stats_speed_; }   // 本帧在补位移的对象数
    double nudge_px() const { return stats_nudge_; }     // 本帧补出去的像素
    int    shield_objs() const { return stats_shield_; } // 本帧带护盾的对象数

    // 每帧重建的移速诊断（只给日志线程看，最多几条）
    struct SpdRow {
        int idx;
        double bonus;
        double own;     // 累计：游戏自己走了多少
        double add;     // 累计：我们补了多少
    };
    const std::vector<SpdRow>& spd_rows() const { return spd_rows_; }

    // 调试：强制某条词条（对应 Python 的 HEX_FORCE）
    std::vector<std::string> force;

private:
    void Scan();
    void EnsureHexes(std::vector<Obj>& objs,
                     std::map<uintptr_t, ObjState>& state_map,
                     Scope scope, const char* kind);
    void ApplyOnSpawn(const Obj& o, ObjState& st, const char* kind);
    void BuildOverlay();

    // 每帧重算类
    void ApplyPlantFrame(const Obj& p, ObjState& st);
    void ApplyZombieFrame(const Obj& z, ObjState& st, bool baron);
    bool Lonely(const Obj& z) const;

    std::mt19937 rng_;
    std::map<uintptr_t, ObjState> plant_state_;
    std::map<uintptr_t, ObjState> zombie_state_;
    std::vector<Obj> plants_;
    std::vector<Obj> zombies_;
    int stats_plant_ = 0;
    int stats_zombie_ = 0;
    int stats_speed_ = 0;
    int stats_shield_ = 0;
    double stats_nudge_ = 0.0;
    std::vector<SpdRow> spd_rows_;
    uint32_t now_ms_ = 0;            // 本帧的 GetTickCount
    uint32_t last_spd_ms_ = 0;       // 上次重建 spd_rows_ 的时刻

    // ---- 面板快照（游戏线程写、面板线程读，靠 ov_mx_ 保护）----
    mutable std::mutex ov_mx_;
    std::vector<OverlayRow> ov_rows_;
    int ov_plants_ = 0;
    int ov_zombies_ = 0;
    uint32_t ov_stamp_ms_ = 0;
    uint32_t last_ov_ms_ = 0;
    uintptr_t last_board_ = 0;
    uint32_t frame_ = 0;
};

// 全局单例，proxy.cpp 直接用
Engine& GetEngine();

}  // namespace hx
