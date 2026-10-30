// ============================================================================
//  hex.h —— 词条定义 / 池子 / 抽卡
//
//  词条表本身在 gen/hex_table.gen.h 里，由 dllmod/gen_hex_table.py 从
//  tools/hextech_core.py 的 HEX_DEFS 生成。改词条只改 Python，别改这里。
// ============================================================================
#pragma once

#include <cstdint>
#include <cstring>
#include <random>
#include <vector>

namespace hx {

enum class Tier { Silver, Gold, Prismatic };
enum class Scope { Plant, Zombie };

struct HexDef {
    const char* id;
    const char* name;
    const char* short_name;
    Tier tier;
    Scope scope;
    const char* apply;      // "all" / "output" / "tank" / "slow" / "sun"
};

// 由 hex_table.gen.h 提供
extern const HexDef kHexDefs[];
extern const int kHexDefCount;

inline int HexIndex(const char* id) {
    for (int i = 0; i < kHexDefCount; ++i)
        if (std::strcmp(kHexDefs[i].id, id) == 0) return i;
    return -1;
}

// ---- 植物分类（和 Python 侧保持一致）----
inline bool InSet(const int* set, int n, int v) {
    for (int i = 0; i < n; ++i) if (set[i] == v) return true;
    return false;
}

inline bool IsOutputPlant(int t) {
    static const int s[] = {0, 5, 7, 8, 10, 18, 21, 26, 28, 29,
                            32, 34, 39, 40, 42, 43, 44, 46, 47};
    return InSet(s, (int)(sizeof(s) / sizeof(s[0])), t);
}
inline bool IsTankPlant(int t) {
    static const int s[] = {3, 23, 30, 36};
    return InSet(s, 4, t);
}
inline bool IsSlowPlant(int t) {
    static const int s[] = {5, 44};
    return InSet(s, 2, t);
}
inline bool IsSunPlant(int t) {
    static const int s[] = {1, 9, 38, 41};
    return InSet(s, 4, t);
}

// 某个植物能抽哪些 apply 类别的词条
inline bool ApplyFits(const char* apply, int plant_type) {
    if (std::strcmp(apply, "output") == 0) return IsOutputPlant(plant_type);
    if (std::strcmp(apply, "tank") == 0) return IsTankPlant(plant_type);
    if (std::strcmp(apply, "slow") == 0) return IsSlowPlant(plant_type);
    if (std::strcmp(apply, "sun") == 0) return IsSunPlant(plant_type);
    return true;   // "all"
}

// 抽卡：每个对象 1~3 个，究极档单独掷一次骰子（8%）
constexpr int kHexMin = 1;
constexpr int kHexMax = 3;
constexpr double kPrismaticChance = 0.08;

inline std::vector<int> PoolFor(Scope scope, int plant_type, bool prismatic) {
    std::vector<int> out;
    for (int i = 0; i < kHexDefCount; ++i) {
        const HexDef& d = kHexDefs[i];
        if (d.scope != scope) continue;
        bool is_prism = (d.tier == Tier::Prismatic);
        if (is_prism != prismatic) continue;
        if (scope == Scope::Plant && !ApplyFits(d.apply, plant_type)) continue;
        out.push_back(i);
    }
    return out;
}

inline std::vector<int> RollHexes(Scope scope, int plant_type,
                                  std::mt19937& rng) {
    std::uniform_int_distribution<int> pick_count(kHexMin, kHexMax);
    int count = pick_count(rng);

    std::vector<int> pool = PoolFor(scope, plant_type, false);
    std::uniform_real_distribution<double> uni(0.0, 1.0);
    if (uni(rng) < kPrismaticChance) {
        std::vector<int> extra = PoolFor(scope, plant_type, true);
        pool.insert(pool.end(), extra.begin(), extra.end());
    }
    if (pool.empty()) return {};

    std::vector<int> out;
    for (int k = 0; k < count && !pool.empty(); ++k) {
        std::uniform_int_distribution<size_t> pick(0, pool.size() - 1);
        size_t idx = pick(rng);
        out.push_back(pool[idx]);
        pool.erase(pool.begin() + (ptrdiff_t)idx);   // 同一对象不重复抽同一条
    }
    return out;
}

}  // namespace hx
