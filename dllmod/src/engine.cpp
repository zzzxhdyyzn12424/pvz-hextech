// ============================================================================
//  engine.cpp —— 词条引擎（进程内版）
//
//  一帧的流程（由 Board::Update 开头的 detour 驱动）：
//      1. Scan()        扫一遍植物 / 僵尸数组，拿本帧快照
//      2. EnsureHexes() 新上场的对象抽词条 + 跑一次性效果（加血上限之类）
//      3. 每帧重算：植物攻速 / 僵尸移速 / 僵尸体型 / 护盾回盾
//
//  已搬完的（本文件里都有实现）：
//      植物：p_swift p_zealot p_rageblade p_bigpea p_titan p_deepfish
//      僵尸：z_goliath z_astral z_mountain z_escape
//            z_lone z_homeguard z_legday z_madman z_smallkiller z_baron
//
//  还没搬的（需要「谁打了谁」的归属信息，见 README M3 清单）：
//      增伤 / 免伤 / 吸血 / 反弹 / 死亡爆炸 / 收集者 / 冰寒 / 电龙魂 …
// ============================================================================
#include "engine.h"

#include <cmath>

#include "log.h"
#include "pvz.h"
#include "tuning.h"

namespace hx {

int gHexIdx[HEX_COUNT];

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
    out.x = RdFast<int>(o + O_X);
    out.y = RdFast<int>(o + O_Y);
    out.w = RdFast<int>(o + O_W);
    out.h = RdFast<int>(o + O_H);
    return true;
}

// 从 +0x5C 的当前读数反推「原版射击间隔」，把倍率一层层剥掉。
//
// 原版只有 150 / 300 两档间隔，所以落在 [140, 400] 之外的读数一定是 mod 写的。
// 为什么要剥：同一株植物可能被我们改过好几次（换了词条、重新抽卡），
// 不剥就会把「改后的值」当原版值再乘一次倍率，越改越偏 ——
// 往大偏是「不射了」，往小偏是「那一行无敌」，都是最危险的方向。
int NativeShootBase(int cur, double mult) {
    double guess = cur;
    for (int i = 0; i < 4; ++i) {          // 最多剥 4 层，避免死循环
        if (guess >= tune::kShootNativeMin && guess <= tune::kShootNativeMax)
            return (int)lround(guess);
        if (mult <= 0.0) break;
        guess *= mult;
    }
    // 剥不出来：按原版那两档就近吸附，比夹到区间边界更接近真值
    return guess < 225.0 ? 150 : 300;
}

// 把「海克斯护盾」装到僵尸的原生护盾槽上。
//
// 游戏自己的伤害流程（0x53184A）是：
//     if (zombie+0xD8 != 0 && !(damageFlags & 1))
//         damage = TakeShieldDamage(damage);   // 扣 +0xDC，溢出才继续
//     if (damage > 0) { 扣头盔(+0xD0)；再扣身体(+0xC8) }
//
// 所以只要把 +0xD8 设成非 0、+0xDC/+0xE0 设成血量，护盾吸收完全交给游戏，
// 我们一帧都不用补血 —— 这比「每帧读血量再补」稳得多。
void ApplyShield(uintptr_t addr, int cur, int maxv, bool force_type) {
    int t = RdFast<int>(addr + Z_SHIELD_TYPE);
    if (force_type || t == 0) Wr<int>(addr + Z_SHIELD_TYPE, tune::Z_SHIELD_TYPE);
    Wr<int>(addr + Z_SHIELD_MAX, maxv);
    Wr<int>(addr + Z_SHIELD, cur);
}

}  // namespace

void Engine::Init(unsigned seed) {
    rng_.seed(seed);
    for (int i = 0; i < HEX_COUNT; ++i) gHexIdx[i] = HexIndex(kHexIdNames[i]);
    Log("[engine] init, %d hex defs, %d excl groups, seed=%u",
        kHexDefCount, kExclGroupCount, seed);
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

// ---------------------------------------------------------------------------
// 一次性效果：上场那一刻改血量
//
// 注意僵尸**没有 hp_max 字段**（+0xCC 是别的东西），所以僵尸的「最大血量」
// 只能是出生时的血量，记在 ObjState::hp_max 里。植物有真实的 +0x44。
// ---------------------------------------------------------------------------
void Engine::ApplyOnSpawn(const Obj& o, ObjState& st, const char* kind) {
    if (o.addr && st.hp_max <= 0) st.hp_max = 1;

    // ---- 泰坦巨人：血量上限 ×3 ----
    if (st.has(HEX_p_titan)) {
        int add = st.hp_max * tune::P_TITAN_HP;
        if (add > 0) {
            st.hp_max += add;
            Wr<int>(o.addr + P_HP_MAX, st.hp_max);
            int nhp = o.hp + add;
            Wr<int>(o.addr + P_HP, nhp);
            st.hp_prev = nhp;
            Log("[hex] %s@%p 泰坦巨人：血量 %d -> %d", kind, (void*)o.addr,
                o.hp, nhp);
        }
    }

    // ---- 大肥鱼：血量顶到 8 万 ----
    if (st.has(HEX_p_deepfish)) {
        st.hp_max = tune::P_FISH_HP;
        Wr<int>(o.addr + P_HP_MAX, st.hp_max);
        Wr<int>(o.addr + P_HP, st.hp_max);
        st.hp_prev = st.hp_max;
        Log("[hex] %s@%p 大肥鱼：血量 %d -> %d", kind, (void*)o.addr,
            o.hp, st.hp_max);
    }

    // ---- 歌莉娅巨人：血量 +100% ----
    if (st.has(HEX_z_goliath)) {
        int add = (int)(st.hp_max * tune::Z_GOLIATH_HP);
        if (add > 0) {
            st.hp_max += add;
            int nhp = o.hp + add;
            Wr<int>(o.addr + Z_HP, nhp);
            st.hp_prev = nhp;
            Log("[hex] %s@%p 歌莉娅巨人：血量 %d -> %d", kind, (void*)o.addr,
                o.hp, nhp);
        }
    }

    // ---- 星界躯体：+1800 血 ----
    if (st.has(HEX_z_astral)) {
        st.hp_max += tune::Z_ASTRAL_HP;
        int nhp = o.hp + tune::Z_ASTRAL_HP;
        Wr<int>(o.addr + Z_HP, nhp);
        st.hp_prev = nhp;
        Log("[hex] %s@%p 星界躯体：血量 %d -> %d", kind, (void*)o.addr,
            o.hp, nhp);
    }
}

void Engine::EnsureHexes(std::vector<Obj>& objs,
                         std::map<uintptr_t, ObjState>& state_map,
                         Scope scope, const char* kind) {
    std::set<uintptr_t> alive;
    for (const Obj& o : objs) {
        alive.insert(o.addr);
        if (state_map.find(o.addr) != state_map.end()) continue;

        ObjState st;
        // 植物有真实的血量上限（+0x44）；僵尸**没有**这个字段（+0xCC 是别的东西，
        // 读出来是垃圾值）。如果照抄 +0xCC，歌莉娅巨人会把血量加到几十万、
        // 逃跑计划的 50% 阈值也全错。所以僵尸一律用出生血量当上限。
        st.hp_max = (scope == Scope::Plant && o.hp_max > 0) ? o.hp_max : o.hp;
        if (st.hp_max <= 0) st.hp_max = 1;
        st.hp_prev = o.hp;

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
            st.hexes = RollHexesEx(scope, o.type, rng_);
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
    // 清理已消失的对象
    for (auto it = state_map.begin(); it != state_map.end();) {
        if (!alive.count(it->first)) it = state_map.erase(it);
        else ++it;
    }
}

// ---------------------------------------------------------------------------
// 植物：攻速
//
// 逆向依据（0x45F8A9 起，Plant::UpdateShooting）：
//     0x45F8A9  add  dword ptr [esi+0x58], -1     ; 倒计时
//     0x45F8B3  jg   跳过
//     0x45F8BA  call 0x5af400                     ; rand(0, 15)
//     0x45F8BF  mov  ecx, [esi+0x5c]              ; 原始射击间隔
//     0x45F8C2  sub  ecx, eax                     ; 间隔 - 抖动
//     0x45F8CA  mov  [esi+0x58], ecx              ; 重置倒计时
//     0x45F917  call 0x45ef10                     ; ★ 真正生成子弹
//
// 所以改 +0x5C 就是改「每隔多少帧射一发」，子弹由游戏自己生成，
// 不是我们补伤害 —— 这是极少数能「真的多射」的词条。
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// 植物每帧
//
// 顺序很重要：先结算「这一帧掉的血」，再干别的。
// 血量记账是整层伤害词条的唯一触发源 —— 没有伤害钩子，掉血就是唯一信号。
// ---------------------------------------------------------------------------
void Engine::ApplyPlantFrame(Obj& p, ObjState& st) {
    int php = RdFast<int>(p.addr + P_HP);
    if (st.hp_prev > 0 && php < st.hp_prev) {
        st.last_hit_ms = now_ms_;
        OnPlantDamaged(p, st, st.hp_prev - php, baron_now_);
        php = RdFast<int>(p.addr + P_HP);
    }
    st.hp_prev = php;

    // ---------- 山脉龙魂：脱战 3 秒回满盾 ----------
    if (st.has(HEX_p_mountain)) {
        if (st.shield_max == 0) {
            st.shield_max = (int)(st.hp_max * tune::P_MOUNTAIN_RATIO);
            st.shield = st.shield_max;
            Log("[hex] plant@%p 山脉龙魂：护盾 %d", (void*)p.addr, st.shield_max);
        } else if (now_ms_ - st.last_hit_ms > tune::P_MOUNTAIN_REGEN_MS) {
            if (st.shield < st.shield_max) st.shield = st.shield_max;
        }
    }

    // ---------- 攻速：改 +0x5C 原生射击间隔（游戏自己多生成子弹）----------
    // 逆向依据见 engine.cpp 顶部注释（Plant::UpdateShooting 0x45F8A9 起）
    double mult = 1.0;
    if (st.has(HEX_p_swift))  mult += tune::P_SWIFT_SPEED;
    if (st.has(HEX_p_zealot)) mult += tune::P_ZEALOT_SPEED;
    if (st.has(HEX_p_rageblade)) {
        double rb = st.rage_hits * tune::P_RAGEBLADE_PER_HIT;
        if (rb > tune::P_RAGEBLADE_CAP) rb = tune::P_RAGEBLADE_CAP;
        mult += rb;
    }
    if (st.has(HEX_p_bigpea)) mult *= (1.0 - tune::P_BIGPEA_SLOW);
    if (std::fabs(mult - 1.0) > 1e-9) {
        uintptr_t a = p.addr + P_SHOOT_INT;
        int cur = RdFast<int>(a);
        if (st.shoot_base == 0 && cur > 1 && cur < 100000)
            st.shoot_base = NativeShootBase(cur, mult);
        if (st.shoot_base > 0) {
            int want = (int)lround(st.shoot_base / mult);
            if (want < tune::P_SHOOT_INT_MIN) want = tune::P_SHOOT_INT_MIN;
            if (cur != want) {
                Wr<int>(a, want);
                if (!st.logged_shoot) {
                    st.logged_shoot = true;
                    Log("[hex] plant@%p type=%d 攻速 ×%.2f：射击间隔 %d -> %d",
                        (void*)p.addr, p.type, mult, st.shoot_base, want);
                }
            }
        }
    }

    // ---------- 大肥鱼：全场索敌 + 5×5 核爆 ----------
    if (st.has(HEX_p_deepfish)) {
        if (st.fish_next_ms == 0) st.fish_next_ms = now_ms_;
        if (now_ms_ >= st.fish_next_ms) {
            st.fish_next_ms = now_ms_ + tune::P_FISH_INTERVAL;
            // 锁离房子最近的（x 最小）
            Obj* tgt = nullptr;
            for (Obj& z : zombies_) if (!tgt || z.x < tgt->x) tgt = &z;
            if (tgt) {
                for (Obj& z : zombies_) {
                    if (std::abs(z.x - tgt->x) > tune::P_FISH_RADIUS_X) continue;
                    if (std::abs(z.y - tgt->y) > tune::P_FISH_RADIUS_Y) continue;
                    auto it = zombie_state_.find(z.addr);
                    if (it != zombie_state_.end())
                        DamageZombie(z, it->second, tune::P_FISH_DMG);
                }
                std::uniform_real_distribution<double> u(0.0, 1.0);
                if (u(rng_) < tune::P_FISH_NUKE_CHANCE) {
                    for (Obj& z : zombies_) {
                        auto it = zombie_state_.find(z.addr);
                        if (it != zombie_state_.end())
                            DamageZombie(z, it->second, tune::P_FISH_NUKE_DMG);
                    }
                    Log("[hex] plant@%p 大肥鱼：全场核爆", (void*)p.addr);
                }
            }
        }
    }

    // ---------- 升级献祭：每秒打前方六格，顺手给阳光 ----------
    if (st.has(HEX_p_sacrifice)) {
        if (st.sac_next_ms == 0) st.sac_next_ms = now_ms_ + tune::P_SACRIFICE_CD;
        if (now_ms_ >= st.sac_next_ms) {
            st.sac_next_ms = now_ms_ + tune::P_SACRIFICE_CD;
            int dmg = (int)(st.hp_max * tune::P_SACRIFICE_RATIO);
            if (dmg < 1) dmg = 1;
            int reach = tune::P_SACRIFICE_CELLS * tune::LAWN_CELL_W;
            for (Obj& z : zombies_) {
                if (std::abs(z.y - p.y) >= tune::ROW_TOL) continue;
                int d = z.x - p.x;
                if (d < -tune::LAWN_CELL_W || d > reach) continue;
                auto it = zombie_state_.find(z.addr);
                if (it != zombie_state_.end()) DamageZombie(z, it->second, dmg);
            }
            AddSun(tune::P_SACRIFICE_SUN);
        }
    }

    // ---------- 守护：每 5 秒给附近一株回 100 血 ----------
    if (st.has(HEX_p_guardian)) {
        if (st.last_guard_ms == 0) st.last_guard_ms = now_ms_;
        if (now_ms_ - st.last_guard_ms >= tune::P_GUARDIAN_CD) {
            st.last_guard_ms = now_ms_;
            std::vector<Obj*> cand;
            for (Obj& o : plants_) if (Near(p, o)) cand.push_back(&o);
            if (!cand.empty()) {
                std::uniform_int_distribution<size_t> d(0, cand.size() - 1);
                Obj* t = cand[d(rng_)];
                auto it = plant_state_.find(t->addr);
                if (it != plant_state_.end()) {
                    int cur = RdFast<int>(t->addr + P_HP);
                    int nv = cur + tune::P_GUARDIAN_HEAL;
                    if (nv > it->second.hp_max) nv = it->second.hp_max;
                    Wr<int>(t->addr + P_HP, nv);
                    it->second.hp_prev = nv;
                    t->hp = nv;
                }
            }
        }
    }

    // ---------- 全心为你：附近植物快死时，向日葵替它去死 ----------
    if (st.has(HEX_p_devotion) && !st.fired.count("devotion")) {
        for (Obj& o : plants_) {
            if (o.addr == p.addr) continue;
            if (!Near(p, o)) continue;
            if (o.hp <= 0 || o.hp > 30) continue;
            auto it = plant_state_.find(o.addr);
            if (it == plant_state_.end()) continue;
            st.fired.insert("devotion");
            Wr<int>(p.addr + P_HP, 0);
            st.hp_prev = 0;
            p.hp = 0;
            int nv = tune::P_DEVOTION_HEAL;
            if (nv > it->second.hp_max) nv = it->second.hp_max;
            Wr<int>(o.addr + P_HP, nv);
            it->second.hp_prev = nv;
            o.hp = nv;
            Log("[hex] plant@%p 全心为你：替 %p 去死", (void*)p.addr, (void*)o.addr);
            break;
        }
    }
}

bool Engine::Lonely(const Obj& z) const {
    for (const Obj& o : zombies_) {
        if (o.addr == z.addr) continue;
        if (std::abs(o.x - z.x) < tune::Z_LONE_RANGE_X &&
            std::abs(o.y - z.y) < tune::Z_LONE_RANGE_Y) return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// 僵尸每帧
// ---------------------------------------------------------------------------
void Engine::ApplyZombieFrame(const Obj& z, ObjState& st, bool baron) {
    const uintptr_t a = z.addr;

    // ---------- 体型缩小 ----------
    double scale = 0.0;
    if (st.has(HEX_z_smallkiller))   scale = tune::Z_SMALLKILLER_SCALE;
    else if (st.has(HEX_z_madman))   scale = tune::Z_MADMAN_SCALE;
    if (scale > 0.0) {
        if (st.base_w <= 0) {
            int w = RdFast<int>(a + O_W), h = RdFast<int>(a + O_H);
            if (w > 0 && h > 0) { st.base_w = w; st.base_h = h; }
        }
        if (st.base_w > 0) {
            int tw = (int)(st.base_w * scale); if (tw < 1) tw = 1;
            int th = (int)(st.base_h * scale); if (th < 1) th = 1;
            if (RdFast<int>(a + O_W) != tw) {
                Wr<int>(a + O_W, tw);
                Wr<int>(a + O_H, th);
            }
        }
    }

    // ---------- 血量记账 → 伤害反应层 ----------
    int hp = RdFast<int>(a + Z_HP);
    if (st.hp_prev > 0 && hp < st.hp_prev) {
        int dmg = st.hp_prev - hp;
        st.hits++;
        st.last_hit_ms = now_ms_;

        // 终极不可阻挡：首次受伤 → 10 秒免疫控制（一次性）
        if (st.has(HEX_z_unstoppable) && !st.fired.count("unstoppable")) {
            st.fired.insert("unstoppable");
            st.cc_until_ms = now_ms_ + (uint32_t)(tune::Z_UNSTOPPABLE_TIME * 1000);
            st.chill_ratio = 0.0;
            st.chill_until_ms = 0;
            Log("[hex] zombie@%p 终极不可阻挡：10 秒免疫控制", (void*)a);
        }
        hp = OnZombieDamaged(z, st, dmg, baron);
    }
    st.hp_prev = hp;

    // 护盾被打也算受伤（否则「脱战回盾」会变成每帧回满 = 无敌）
    int cur_shield = RdFast<int>(a + Z_SHIELD);
    if (cur_shield < st.shield) st.last_hit_ms = now_ms_;
    st.shield = cur_shield;
    if (cur_shield > 0) stats_shield_++;

    // ---------- 山脉龙魂 ----------
    if (st.has(HEX_z_mountain)) {
        if (st.shield_max == 0) {
            st.shield_max = (int)(st.hp_max * tune::P_MOUNTAIN_RATIO);
            st.shield = st.shield_max;
            ApplyShield(a, st.shield, st.shield_max, false);
            Log("[hex] zombie@%p 山脉龙魂：护盾 %d", (void*)a, st.shield_max);
        } else if (now_ms_ - st.last_hit_ms > tune::P_MOUNTAIN_REGEN_MS) {
            if (cur_shield < st.shield_max) {
                Wr<int>(a + Z_SHIELD, st.shield_max);
                st.shield = st.shield_max;
            }
        }
    }

    // ---------- 逃跑计划 ----------
    if (st.has(HEX_z_escape) && !st.fired.count("escape")) {
        if (hp > 0 && hp <= (int)(st.hp_max * tune::Z_ESCAPE_RATIO)) {
            st.fired.insert("escape");
            st.escape_ms = now_ms_;
            st.escape_tick_ms = now_ms_;
            int sm = (int)(st.hp_max * tune::Z_ESCAPE_SHIELD);
            if (sm > st.shield_max) st.shield_max = sm;
            st.shield = st.shield_max;
            ApplyShield(a, st.shield, st.shield_max, true);
            Log("[hex] zombie@%p 逃跑计划：护盾 %d", (void*)a, st.shield_max);
        }
    }
    if (st.fired.count("escape") && st.shield_max > 0) {
        uint32_t dt = now_ms_ - st.escape_tick_ms;
        if (dt > 100) dt = 100;
        st.escape_tick_ms = now_ms_;
        int dec = (int)(st.shield_max * (double)dt / (double)tune::kEscapeDecayMs + 0.5);
        int cur = RdFast<int>(a + Z_SHIELD);
        if (cur > 0 && dec > 0) {
            int nv = cur - dec;
            if (nv < 0) nv = 0;
            Wr<int>(a + Z_SHIELD, nv);
            st.shield = nv;
        }
    }

    // ---------- 死之舞：流血 ----------
    if (st.dot_rate > 0) {
        if (now_ms_ < st.dot_until_ms) {
            double dt = 1.0 / 30.0;
            st.regen_acc += st.dot_rate * dt;     // 借用累加器，避免小数值被取整吃掉
            int n = (int)st.regen_acc;
            if (n > 0) { st.regen_acc -= n; DamageZombie(z, st, n); }
        } else {
            st.dot_rate = 0;
        }
    }

    // ---------- 水龙魂：受伤后或正在啃食时回血 ----------
    if (st.has(HEX_z_waterdragon)) {
        bool hurt = (now_ms_ - st.last_hit_ms) <= tune::Z_WATERDRAGON_WINDOW;
        bool biting = (now_ms_ - st.last_bite_ms) <= 1000;
        int cur = RdFast<int>(a + Z_HP);
        if ((hurt || biting) && cur < st.hp_max) {
            st.regen_acc += tune::Z_WATERDRAGON_REGEN / 30.0;
            if (st.regen_acc >= 1.0) {
                int n = (int)st.regen_acc;
                st.regen_acc -= n;
                HealZombie(z, st, n);
            }
        } else {
            st.regen_acc = 0.0;
        }
    }

    // ---------- 移速：补位移（+0x2C），并把冰寒的额外减速一起算进去 ----------
    double k = 0.0;
    if (st.has(HEX_z_lone) && Lonely(z)) k += tune::Z_LONE_SPEED;
    if (st.has(HEX_z_homeguard) && st.hits == 0) k += tune::Z_HOMEGUARD_SPEED;
    if (st.has(HEX_z_legday))      k += tune::Z_LEGDAY_SPEED;
    if (st.has(HEX_z_madman))      k += tune::Z_MADMAN_SPEED;
    if (st.has(HEX_z_smallkiller)) k += tune::Z_SMALLKILLER_SPEED;
    if (baron)                     k += tune::Z_BARON_SPEED;
    if (st.fired.count("escape")) {
        uint32_t el = now_ms_ - st.escape_ms;
        double left = (el >= tune::kEscapeDecayMs)
                      ? 0.0 : 1.0 - (double)el / tune::kEscapeDecayMs;
        k += tune::Z_ESCAPE_SPEED * left;
    }
    // 冰寒：在原生减速之上再压一段位移（负值 = 拖慢）
    if (st.chill_ratio > 0 && now_ms_ < st.chill_until_ms &&
        !CcImmune(st) && !st.has(HEX_z_legday)) {
        k -= st.chill_ratio;
    }

    if (std::fabs(k) < 1e-9) {
        st.fx_ok = false;
    } else {
        float fx = RdFast<float>(a + O_FX);
        if (fx > -100000.0f && fx < 100000.0f) {
            if (!st.fx_ok) {
                st.prev_fx = fx;
                st.fx_ok = true;
            } else {
                double own = (double)fx - (double)st.prev_fx;
                double add = own * k;
                if (add >  tune::Z_NUDGE_MAX_PER_FRAME) add =  tune::Z_NUDGE_MAX_PER_FRAME;
                if (add < -tune::Z_NUDGE_MAX_PER_FRAME) add = -tune::Z_NUDGE_MAX_PER_FRAME;
                float nv = fx + (float)add;
                Wr<float>(a + O_FX, nv);
                st.prev_fx = nv;
                stats_speed_++;
                stats_nudge_ += add;
                st.sum_own += own;
                st.sum_add += add;
                if (now_ms_ - last_spd_ms_ < 500 && spd_rows_.size() < 6) {
                    SpdRow r;
                    r.idx = z.idx;
                    r.bonus = k;
                    r.own = st.sum_own;
                    r.add = st.sum_add;
                    spd_rows_.push_back(r);
                }
            }
        }
    }

    // ---------- 闪现向前：越过当前植物，每只 2 次 ----------
    if (st.has(HEX_z_flash)) {
        if (st.flash_left < 0) st.flash_left = tune::Z_FLASH_CHARGES;
        if (st.flash_left > 0) {
            bool adjacent = false;   // 注意：变量不能叫 near/far —— MinGW 头文件里有同名宏
            for (const Obj& p : plants_) {
                if (std::abs(p.y - z.y) >= tune::ROW_TOL) continue;
                int d = z.x - p.x;
                if (d >= 0 && d <= 70) { adjacent = true; break; }
            }
            if (adjacent) {
                st.flash_left--;
                NudgeX(a, -tune::Z_FLASH_DIST);
                st.fx_ok = false;
                st.chill_ratio = 0.0;
                Log("[hex] zombie@%p 闪现向前（剩 %d 次）", (void*)a, st.flash_left);
            }
        }
    }

    // ---------- 万用瞄准镜：隔一格 / 隔两格也能吃到 ----------
    if (st.has(HEX_z_scope) || st.has(HEX_z_scope2)) {
        bool has_close = false;
        for (const Obj& p : plants_) {
            if (std::abs(p.y - z.y) >= tune::ROW_TOL) continue;
            int d = z.x - p.x;
            if (d >= 0 && d <= 70) { has_close = true; break; }
        }
        if (!has_close) {
            int reach = st.has(HEX_z_scope2) ? tune::Z_SCOPE_REACH
                                             : tune::Z_SCOPE_REACH_1;
            Obj* tgt = nullptr;
            for (Obj& p : plants_) {
                if (std::abs(p.y - z.y) >= tune::ROW_TOL) continue;
                int d = z.x - p.x;
                if (d > 70 && d <= reach) {
                    if (!tgt || p.x > tgt->x) tgt = &p;
                }
            }
            if (tgt) {
                auto it = plant_state_.find(tgt->addr);
                if (it != plant_state_.end()) {
                    st.last_bite_ms = now_ms_;
                    st.scope_acc += tune::Z_SCOPE_DPS / 30.0;
                    int bite = (int)st.scope_acc;
                    if (bite > 0) {
                        st.scope_acc -= bite;
                        DamagePlant(*tgt, it->second, bite);
                    }
                }
            }
        }
    }

    // ---------- 死亡：小丑学院 + 收集者 ----------
    if (hp <= 0 && !st.fired.count("killed")) {
        st.fired.insert("killed");
        if (st.has(HEX_z_clowncollege) && !st.fired.count("clown")) {
            st.fired.insert("clown");
            ClownBurst(z);
        }
    }
}

void Engine::Tick() {
    ++frame_;
    now_ms_ = GetTickCount();
    Scan();
    EnsureHexes(plants_, plant_state_, Scope::Plant, "plant");
    EnsureHexes(zombies_, zombie_state_, Scope::Zombie, "zombie");
    stats_plant_ = (int)plants_.size();
    stats_zombie_ = (int)zombies_.size();
    stats_speed_ = 0;
    stats_shield_ = 0;
    stats_nudge_ = 0.0;
    if (now_ms_ - last_spd_ms_ >= 500) {
        spd_rows_.clear();
        last_spd_ms_ = now_ms_;
    }

    // 男爵之手：场上只要有一只带这词条的僵尸，全体僵尸一起吃光环
    baron_now_ = false;
    for (const Obj& z : zombies_) {
        auto it = zombie_state_.find(z.addr);
        if (it != zombie_state_.end() && it->second.has(HEX_z_baron)) {
            baron_now_ = true;
            break;
        }
    }

    ComputeAura();

    for (Obj& p : plants_) {
        auto it = plant_state_.find(p.addr);
        if (it != plant_state_.end()) ApplyPlantFrame(p, it->second);
    }
    for (const Obj& z : zombies_) {
        auto it = zombie_state_.find(z.addr);
        if (it != zombie_state_.end()) ApplyZombieFrame(z, it->second, baron_now_);
    }

    // 覆盖层快照每帧重建（全是 POD，ov_tmp_ 复用容量）
    last_ov_ms_ = now_ms_;
    BuildOverlay();
}

// ---------------------------------------------------------------------------
// 覆盖层快照
//
// 这里只搬数字，不拼字符串 —— 每帧都跑，拼字符串会拖慢游戏线程。
// 名字、词条短名、档位颜色由 UI 线程自己去静态表查。
// ---------------------------------------------------------------------------
void Engine::BuildOverlay() {
    OverlaySnap& d = ov_tmp_;
    d.objs.clear();

    auto add = [&](const Obj& o, std::map<uintptr_t, ObjState>& sm, bool plant) {
        auto it = sm.find(o.addr);
        if (it == sm.end()) return;
        const ObjState& st = it->second;

        OverlayObj r;
        r.plant  = plant;
        r.idx    = o.idx;
        r.type   = o.type;
        r.hp     = o.hp;
        r.hp_max = st.hp_max;
        r.x = o.x; r.y = o.y; r.w = o.w; r.h = o.h;
        r.row = o.row; r.col = o.col;

        if (!plant) {
            // 僵尸的盾 / 头盔直接读内存（这两个是游戏自己的字段，
            // 山脉龙魂和逃跑计划就是把盾写在这里）
            r.shield      = RdFast<int>(o.addr + Z_SHIELD);
            r.shield_max  = RdFast<int>(o.addr + Z_SHIELD_MAX);
            r.helmet      = RdFast<int>(o.addr + Z_HELMET);
            r.helmet_max  = RdFast<int>(o.addr + Z_HELMET_MAX);
        } else {
            r.shield = st.shield;
            r.shield_max = st.shield_max;
        }

        r.nhex = 0;
        for (int h : st.hexes) {
            if (r.nhex >= 3) break;
            r.hex[r.nhex++] = h;
        }
        d.objs.push_back(r);
    };

    for (const Obj& p : plants_)  add(p, plant_state_,  true);
    for (const Obj& z : zombies_) add(z, zombie_state_, false);

    d.plants  = (int)plants_.size();
    d.zombies = (int)zombies_.size();
    d.sun     = last_board_ ? Rd<int>(last_board_ + B_SUN) : 0;
    d.level   = last_board_ ? Rd<int>(last_board_ + B_LEVEL) : 0;
    d.stamp_ms = now_ms_;

    std::lock_guard<std::mutex> lk(ov_mx_);
    ov_snap_.objs.swap(d.objs);
    ov_snap_.plants  = d.plants;
    ov_snap_.zombies = d.zombies;
    ov_snap_.sun     = d.sun;
    ov_snap_.level   = d.level;
    ov_snap_.stamp_ms = d.stamp_ms;
}

void Engine::CopyOverlay(OverlaySnap& out) const {
    std::lock_guard<std::mutex> lk(ov_mx_);
    out = ov_snap_;
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
