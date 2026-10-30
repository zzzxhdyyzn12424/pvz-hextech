// ============================================================================
//  engine.h —— 词条引擎（进程内版）
//
//  对应外置版的 Engine（tools/hextech_core.py）。差别：
//    · 没有 ReadProcessMemory，全部直接解引用
//    · 没有 overlay / tkinter，UI 以后用游戏自己的绘制
//    · 每帧由我们自己的线程驱动（M4 再改成挂进游戏 Update）
// ============================================================================
#pragma once

#include <algorithm>
#include <cstdint>
#include <map>
#include <random>
#include <set>
#include <string>
#include <vector>

#include "hex.h"

namespace hx {

// 一个对象（植物或僵尸）本帧的快照
struct Obj {
    uintptr_t addr = 0;
    int idx = 0;
    int type = 0;
    int hp = 0;
    int hp_max = 0;
    int row = 0;
    int col = 0;
    int x = 0;
    int y = 0;
};

// 每个对象挂的词条状态
struct ObjState {
    std::vector<int> hexes;          // 词条下标
    std::set<std::string> fired;     // 一次性效果是否已触发
    bool has(const char* id) const {
        int i = HexIndex(id);
        return i >= 0 && std::find(hexes.begin(), hexes.end(), i) != hexes.end();
    }
};

class Engine {
public:
    void Init(unsigned seed);

    // 每帧调用。返回本帧是否有新对象拿到词条（用于日志）
    void Tick();

    int plant_count() const { return stats_plant_; }
    int zombie_count() const { return stats_zombie_; }

    // 调试：强制某条词条（对应 Python 的 HEX_FORCE）
    std::vector<std::string> force;

private:
    void Scan();
    void EnsureHexes(std::vector<Obj>& objs,
                     std::map<uintptr_t, ObjState>& state_map,
                     Scope scope, const char* kind);
    void ApplyOnSpawn(const Obj& o, ObjState& st, const char* kind);

    std::mt19937 rng_;
    std::map<uintptr_t, ObjState> plant_state_;
    std::map<uintptr_t, ObjState> zombie_state_;
    std::vector<Obj> plants_;
    std::vector<Obj> zombies_;
    int stats_plant_ = 0;
    int stats_zombie_ = 0;
    uintptr_t last_board_ = 0;
};

// 全局单例，proxy.cpp 直接用
Engine& GetEngine();

}  // namespace hx
