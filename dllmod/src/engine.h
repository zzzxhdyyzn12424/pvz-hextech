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
    int w = 0;
    int h = 0;
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

    // ---- 战斗记账 ----
    int resolve_stacks = 0;          // 泰坦的坚决：已经挨了几下
    double dot_rate = 0.0;           // 死之舞：每秒流血
    uint32_t dot_until_ms = 0;
    double regen_acc = 0.0;          // 水龙魂：回血累加器
    double scope_acc = 0.0;          // 万用瞄准镜：远程啃食累加器
    uint32_t last_aoe_ms = 0;        // 电龙魂：上次溅射时刻
    uint32_t last_bite_ms = 0;       // 水龙魂：上次啃食时刻
    uint32_t chill_until_ms = 0;     // 冰寒：强化减速截止
    double   chill_ratio = 0.0;      // 冰寒：额外压掉多少比例的位移
    uint32_t cc_until_ms = 0;        // 终极不可阻挡：免疫控制截止
    uint32_t last_guard_ms = 0;      // 守护：上次回血时刻
    uint32_t fish_next_ms = 0;       // 大肥鱼：下次出手时刻
    uint32_t sac_next_ms = 0;        // 升级献祭：下次结算时刻
    int flash_left = -1;             // 闪现向前：剩余次数（-1 = 未初始化）

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

    // 覆盖层用的对象快照。**不带字符串** —— 每帧重建，拼字符串太慢。
    // 名字 / 词条短名 / 档位颜色都由覆盖层自己去静态表里查。
    struct OverlayObj {
        bool plant = false;
        int idx = 0;
        int type = 0;
        int hp = 0;
        int hp_max = 0;
        int shield = 0;
        int shield_max = 0;
        int helmet = 0;
        int helmet_max = 0;
        int x = 0, y = 0, w = 0, h = 0;   // 客户区坐标
        int row = 0, col = 0;
        int nhex = 0;
        int hex[3] = { -1, -1, -1 };
    };

    struct OverlaySnap {
        std::vector<OverlayObj> objs;
        int plants = 0;
        int zombies = 0;
        int sun = 0;
        int level = 0;
        uint32_t stamp_ms = 0;
    };

    // 跨线程取覆盖层快照（内部加锁）
    void CopyOverlay(OverlaySnap& out) const;

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

    // ---- 战斗结算（combat.cpp）----
    // 注意：这些函数会直接写内存，并且**同步 hp_prev**。
    // 不同步的话下一帧会把我们自己造成的伤害当成一次新的命中，
    // 增伤会被反复叠乘成无限伤害（外置版踩过这个坑）。
    int  OnZombieDamaged(const Obj& z, ObjState& st, int dmg, bool baron);
    void OnPlantDamaged(Obj& p, ObjState& st, int dmg, bool baron);
    void PlantAfterHit(const Obj& p, ObjState& pst, const Obj& z, int dmg);
    void ZombieAttackPlant(const Obj& z, ObjState& zst, Obj& p, ObjState& pst,
                           int dmg, bool baron);
    void KnockCell(const Obj& z, double px);
    void NudgeX(uintptr_t addr, double px);
    void DamageZombie(const Obj& z, ObjState& st, int amount);
    void DamagePlant(const Obj& p, ObjState& st, int amount);
    void HealZombie(const Obj& z, ObjState& st, int amount);
    void ClownBurst(const Obj& z);
    void AddSun(int delta);
    Obj*  FindPlant(uintptr_t addr);
    Obj*  FindZombie(uintptr_t addr);
    const Obj* AttributePlant(const Obj& z) const;   // 这一下是谁打的
    Obj*  AttributeEater(const Obj& p);              // 谁在啃这株
    void ComputeAura();
    void AuraFor(uintptr_t addr, double& mult, double& dr) const;
    bool Near(const Obj& a, const Obj& b) const;

    // 每帧重算类
    void ApplyPlantFrame(Obj& p, ObjState& st);
    void ApplyZombieFrame(const Obj& z, ObjState& st, bool baron);
    bool Lonely(const Obj& z) const;
    bool CcImmune(const ObjState& st) const {
        return st.cc_until_ms != 0 && now_ms_ < st.cc_until_ms;
    }
    bool baron_now_ = false;         // 本帧场上是否有男爵之手

    std::mt19937 rng_;
    // 向日葵光环：addr -> (伤害倍率, 免伤)
    std::map<uintptr_t, std::pair<double, double>> aura_;
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

    // ---- 覆盖层快照（游戏线程写、UI 线程读，靠 ov_mx_ 保护）----
    mutable std::mutex ov_mx_;
    OverlaySnap ov_snap_;
    OverlaySnap ov_tmp_;        // 复用容量，避免每帧分配
    uint32_t last_ov_ms_ = 0;
    uintptr_t last_board_ = 0;
    uint32_t frame_ = 0;
};

// 全局单例，proxy.cpp 直接用
Engine& GetEngine();

}  // namespace hx
