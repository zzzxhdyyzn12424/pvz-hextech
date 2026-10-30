// ============================================================================
//  engine.cpp —— 词条引擎实现（M3 第一步）
//
//  这一版只做三件事：
//    1. 扫描植物 / 僵尸
//    2. 新对象上场时抽 1~3 条词条
//    3. 把「一次性、纯写字段」的词条真正应用上去（泰坦巨人 / 大肥鱼 / 歌莉娅巨人）
//
//  其余词条需要每帧重算或范围伤害，按表往下搬，见 README 的 M3 清单。
// ============================================================================
#include "engine.h"

#include <algorithm>

#include "hex_table.gen.h"
#include "log.h"
#include "pvz.h"

namespace hx {

namespace {

// 从数组里逐个读出一个对象。调用方已用 SpanSlots 确认整段可读，
// 所以这里用 RdFast，不再逐个字段做 VirtualQuery。
bool ReadObj(uintptr_t base, int i, uint32_t stride, uintptr_t app,
             uintptr_t board, uint32_t dead_off, uint32_t hp_off, Obj& out) {
    uintptr_t o = base + (uintptr_t)i * stride;
    if (RdFast<uint32_t>(o) != (uint32_t)app) return false;        // GameObject 指纹
    if (RdFast<uint32_t>(o + 4) != (uint32_t)board) return false;
    if (RdFast<uint8_t>(o + dead_off)) return false;
    int hp = RdFast<int>(o + hp_off);
    if (hp <= 0) return false;
    out.addr = o;
    out.idx = i;
    out.hp = hp;
    out.hp_max = RdFast<int>(o + hp_off + 4);
    out.type = RdFast<int>(o + 0x24);
    out.row = RdFast<int>(o + 0x1C);
    out.col = RdFast<int>(o + 0x28);
    out.x = RdFast<int>(o + 0x08);
    out.y = RdFast<int>(o + 0x0C);
    return true;
}

}  // namespace

void Engine::Init(unsigned seed) {
    rng_.seed(seed);
    Log("[engine] init, %d hex defs, seed=%u", kHexDefCount, seed);
}

void Engine::Scan() {
    plants_.clear();
    zombies_.clear();

    uintptr_t app = Rd<uint32_t>(kGLawnApp);
    if (!Plausible(app)) return;
    uintptr_t board = Ptr(app, kOffBoard);
    if (!board) return;

    // 换关卡了：Board 地址变了，所有对象状态作废
    if (board != last_board_) {
        if (last_board_) Log("[engine] board 变化 %p -> %p，清空状态",
                             (void*)last_board_, (void*)board);
        last_board_ = board;
        plant_state_.clear();
        zombie_state_.clear();
    }

    uintptr_t pbase = Ptr(board, B_PLANT);
    int pcap = Rd<int>(board + B_PLANT_MAX);
    if (pbase && pcap > 0) {
        if (pcap > MAX_PLANTS) pcap = MAX_PLANTS;
        pcap = SpanSlots(pbase, PLANT_STRIDE, pcap);
        for (int i = 0; i < pcap; ++i) {
            Obj o;
            if (ReadObj(pbase, i, PLANT_STRIDE, app, board, P_DEAD, P_HP, o))
                plants_.push_back(o);
        }
    }

    uintptr_t zbase = Ptr(board, B_ZOMBIE);
    int zcap = Rd<int>(board + B_ZOMBIE_MAX);
    if (zbase && zcap > 0) {
        if (zcap > MAX_ZOMBIES) zcap = MAX_ZOMBIES;
        zcap = SpanSlots(zbase, ZOMBIE_STRIDE, zcap);
        for (int i = 0; i < zcap; ++i) {
            Obj o;
            if (ReadObj(zbase, i, ZOMBIE_STRIDE, app, board, Z_DEAD, Z_HP, o))
                zombies_.push_back(o);
        }
    }
}

void Engine::ApplyOnSpawn(const Obj& o, ObjState& st, const char* kind) {
    // ---- 泰坦巨人：血量上限 ×3 ----
    if (st.has("p_titan") && !st.fired.count("titan")) {
        st.fired.insert("titan");
        int new_max = o.hp_max * 3;
        Wr<int>(o.addr + P_HP_MAX, new_max);
        Wr<int>(o.addr + P_HP, new_max);
        Log("[hex] %s@%p 泰坦巨人：血量 %d -> %d", kind, (void*)o.addr,
            o.hp_max, new_max);
    }

    // ---- 大肥鱼：血量顶到 8 万 ----
    if (st.has("p_deepfish") && !st.fired.count("fish")) {
        st.fired.insert("fish");
        constexpr int kFishHp = 80000;
        Wr<int>(o.addr + P_HP_MAX, kFishHp);
        Wr<int>(o.addr + P_HP, kFishHp);
        Log("[hex] %s@%p 大肥鱼：血量 %d -> %d", kind, (void*)o.addr,
            o.hp, kFishHp);
    }

    // ---- 歌莉娅巨人：血量 ×2 ----
    if (st.has("z_goliath") && !st.fired.count("goliath")) {
        st.fired.insert("goliath");
        int new_max = o.hp_max * 2;
        Wr<int>(o.addr + Z_HP, new_max);
        Log("[hex] %s@%p 歌莉娅巨人：血量 %d -> %d", kind, (void*)o.addr,
            o.hp, new_max);
    }
}

void Engine::EnsureHexes(std::vector<Obj>& objs,
                         std::map<uintptr_t, ObjState>& state_map,
                         Scope scope, const char* kind) {
    std::set<uintptr_t> alive;
    for (const Obj& o : objs) {
        alive.insert(o.addr);
        auto it = state_map.find(o.addr);
        if (it == state_map.end()) {
            ObjState st;

            // HEX_FORCE：调试用，强制抽指定词条
            if (!force.empty()) {
                for (const std::string& id : force) {
                    int idx = HexIndex(id.c_str());
                    if (idx < 0) continue;
                    if (kHexDefs[idx].scope != scope) continue;
                    st.hexes.push_back(idx);
                }
            }
            if (st.hexes.empty()) {
                st.hexes = RollHexes(scope, o.type, rng_);
            }

            std::string names;
            for (int h : st.hexes) {
                if (!names.empty()) names += " / ";
                names += kHexDefs[h].name;
            }
            Log("[roll] %s idx=%d type=%d @%p  ->  %s", kind, o.idx, o.type,
                (void*)o.addr, names.empty() ? "(空)" : names.c_str());

            ApplyOnSpawn(o, st, kind);
            state_map.emplace(o.addr, std::move(st));
        }
    }
    // 清理已消失的对象
    for (auto it = state_map.begin(); it != state_map.end();) {
        if (!alive.count(it->first)) it = state_map.erase(it);
        else ++it;
    }
}

void Engine::Tick() {
    Scan();
    EnsureHexes(plants_, plant_state_, Scope::Plant, "plant");
    EnsureHexes(zombies_, zombie_state_, Scope::Zombie, "zombie");
    stats_plant_ = (int)plants_.size();
    stats_zombie_ = (int)zombies_.size();
}

Engine& GetEngine() {
    static Engine e;
    return e;
}

// ---------------------------------------------------------------------------
// 游戏每帧回调（detour.S 调进来）
//
// 这个函数跑在**游戏主线程**里，每帧一次。要求：
//   · 不能慢 —— 只有字段读写和少量分支
//   · 不能抛异常、不能长时间持锁
//   · 必须防重入（万一游戏在 Update 里又触发了什么）
// ---------------------------------------------------------------------------
extern "C" void hx_OnFrame() {
    static volatile long busy = 0;
    if (InterlockedExchange(&busy, 1)) return;
    hx::GetEngine().Tick();
    InterlockedExchange(&busy, 0);
}

}  // namespace hx
