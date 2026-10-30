// ============================================================================
//  combat.cpp —— 伤害反应类词条
//
//  这一层的核心是一个「穷人的伤害钩子」：
//
//      每帧比对血量和上一帧的差值 → 掉血了就是挨了一下
//
//  没有 hook 游戏的伤害流程（0x53184A），所以拿不到「谁打的、打的什么类型」。
//  归属只能靠几何反查：同一行、在僵尸左边、最靠右的输出植物。
//  外置版用的就是这套，误差在于同一帧里多颗豌豆打同一只僵尸会算成一次命中，
//  但增伤是按比例算的，结果仍然正确。
//
//  ★ 一条铁律：所有「我们自己写进去的血量」都必须同步 hp_prev，
//    否则下一帧会把这次伤害当成新的一次命中，增伤反复叠乘 → 无限伤害。
//    外置版 v1.6.3 就是因为这个把僵尸秒了。
// ============================================================================
#include "engine.h"

#include <cmath>

#include "log.h"
#include "pvz.h"
#include "tuning.h"

namespace hx {

namespace {
inline int Clamp(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }
}  // namespace

// ---------------------------------------------------------------------------
//  基础工具
// ---------------------------------------------------------------------------
Obj* Engine::FindPlant(uintptr_t addr) {
    for (Obj& o : plants_) if (o.addr == addr) return &o;
    return nullptr;
}

Obj* Engine::FindZombie(uintptr_t addr) {
    for (Obj& o : zombies_) if (o.addr == addr) return &o;
    return nullptr;
}

bool Engine::Near(const Obj& a, const Obj& b) const {
    return std::abs(a.x - b.x) <= tune::AURA_RANGE_X &&
           std::abs(a.y - b.y) <= tune::AURA_RANGE_Y;
}

// 谁打的这一下：同一行、在僵尸左边、最靠右的输出植物
const Obj* Engine::AttributePlant(const Obj& z) const {
    const Obj* best = nullptr;
    for (const Obj& p : plants_) {
        if (!IsOutputPlant(p.type)) continue;
        if (std::abs(p.y - z.y) >= tune::ROW_TOL) continue;
        if (p.x > z.x + 20) continue;
        if (!best || p.x > best->x) best = &p;
    }
    return best;
}

// 谁在啃这株：同一行、水平最近（70px 内）
Obj* Engine::AttributeEater(const Obj& p) {
    Obj* best = nullptr;
    int best_d = 70;
    for (Obj& z : zombies_) {
        if (std::abs(z.y - p.y) >= tune::ROW_TOL) continue;
        int d = std::abs(z.x - p.x);
        if (d < best_d) { best = &z; best_d = d; }
    }
    return best;
}

// 向日葵光环：鼓舞（伤害 +20%）/ 风语者祝福（免伤 25%）
void Engine::ComputeAura() {
    aura_.clear();
    for (const Obj& p : plants_) {
        double mult = 1.0, dr = 0.0;
        for (const Obj& s : plants_) {
            if (s.addr == p.addr) continue;
            if (!IsSunPlant(s.type)) continue;
            if (!Near(s, p)) continue;
            auto it = plant_state_.find(s.addr);
            if (it == plant_state_.end()) continue;
            if (it->second.has(HEX_p_inspire))   mult += tune::P_INSPIRE_DMG;
            if (it->second.has(HEX_p_windbless)) dr = std::max(dr, tune::P_WIND_DR);
        }
        aura_[p.addr] = std::make_pair(mult, dr);
    }
}

void Engine::AuraFor(uintptr_t addr, double& mult, double& dr) const {
    auto it = aura_.find(addr);
    if (it == aura_.end()) { mult = 1.0; dr = 0.0; return; }
    mult = it->second.first;
    dr = it->second.second;
}

// 直接写 float 坐标 + 同步整数镜像。
// 为什么不写 mX(+0x08)：mX 每帧被浮点坐标取整刷新，写整数会在下一帧被盖掉，
// 而且整数步进太粗（击退 80px 会被取整误差吃掉）。
void Engine::NudgeX(uintptr_t addr, double px) {
    if (std::fabs(px) < 0.01) return;
    float fx = RdFast<float>(addr + O_FX);
    if (!(fx > -1e6f && fx < 1e6f)) fx = (float)RdFast<int>(addr + O_X);
    float nv = fx + (float)px;
    Wr<float>(addr + O_FX, nv);
    Wr<int>(addr + O_X, (int)lround(nv));
}

// 大腕：把「命中僵尸所在那一格」里的所有僵尸一起往右推一格。
// 设计原文是「那一格」，不是「那一只」—— 只推挨打的那只会让一堆叠着的
// 僵尸里后面的照样贴在植物脸上，名不副实。
void Engine::KnockCell(const Obj& z, double px) {
    int col = (z.x - tune::LAWN_XMIN) / tune::LAWN_CELL_W;
    col = Clamp(col, 0, tune::LAWN_COLS - 1);
    int x0 = tune::LAWN_XMIN + col * tune::LAWN_CELL_W;
    int x1 = x0 + tune::LAWN_CELL_W;

    for (Obj& o : zombies_) {
        if (std::abs(o.y - z.y) >= tune::ROW_TOL) continue;
        if (!(x0 <= o.x && o.x < x1)) continue;
        auto it = zombie_state_.find(o.addr);
        if (it != zombie_state_.end() && CcImmune(it->second)) continue;  // 免疫击退
        NudgeX(o.addr, px);
        o.x = RdFast<int>(o.addr + O_X);
        if (it != zombie_state_.end()) {
            // 瞬移了一段，别让移速自适应把这段当成「它自己走的」
            it->second.fx_ok = false;
            it->second.chill_ratio = 0.0;
        }
    }
}

// ---------------------------------------------------------------------------
//  写血量（全部同步 hp_prev）
// ---------------------------------------------------------------------------
void Engine::DamageZombie(const Obj& z, ObjState& st, int amount) {
    if (amount <= 0) return;
    int cur = RdFast<int>(z.addr + Z_HP);
    int nv = cur - amount;
    if (nv < 0) nv = 0;
    Wr<int>(z.addr + Z_HP, nv);
    st.hp_prev = nv;
    Obj* o = FindZombie(z.addr);
    if (o) o->hp = nv;
}

void Engine::DamagePlant(const Obj& p, ObjState& st, int amount) {
    if (amount <= 0) return;
    int cur = RdFast<int>(p.addr + P_HP);
    int nv = cur - amount;
    if (nv < 0) nv = 0;
    Wr<int>(p.addr + P_HP, nv);
    st.hp_prev = nv;
    Obj* o = FindPlant(p.addr);
    if (o) o->hp = nv;
}

void Engine::HealZombie(const Obj& z, ObjState& st, int amount) {
    if (amount < 1) return;
    int cur = RdFast<int>(z.addr + Z_HP);
    int nv = cur + amount;
    if (nv > st.hp_max) nv = st.hp_max;
    Wr<int>(z.addr + Z_HP, nv);
    st.hp_prev = nv;
    Obj* o = FindZombie(z.addr);
    if (o) o->hp = nv;
}

void Engine::AddSun(int delta) {
    if (!last_board_) return;
    int cur = Rd<int>(last_board_ + B_SUN);
    int nv = cur + delta;
    if (nv < 0) nv = 0;
    Wr<int>(last_board_ + B_SUN, nv);
}

// 小丑学院：死在原地炸一圈。
// 注意不是「真的召唤一只小丑僵尸」—— 那要走 Board::AddZombie，得手工构造
// 整个 Zombie 对象（reanim / 音效 / 状态机），失败直接崩游戏。
// 保留玩法核心：原位范围伤害。
void Engine::ClownBurst(const Obj& z) {
    for (Obj& p : plants_) {
        if (std::abs(p.x - z.x) > tune::Z_CLOWN_RADIUS) continue;
        if (std::abs(p.y - z.y) > tune::Z_CLOWN_RADIUS) continue;
        auto it = plant_state_.find(p.addr);
        if (it == plant_state_.end()) continue;
        DamagePlant(p, it->second, tune::Z_CLOWN_DMG);
    }
    Log("[hex] zombie@%p 小丑学院：原地爆炸", (void*)z.addr);
}

// ---------------------------------------------------------------------------
//  植物打僵尸这一下：增伤 + 附带效果
//  返回最终血量
// ---------------------------------------------------------------------------
int Engine::OnZombieDamaged(const Obj& z, ObjState& st, int dmg, bool baron) {
    int hp = RdFast<int>(z.addr + Z_HP);
    const Obj* src = AttributePlant(z);
    ObjState* pst = nullptr;
    if (src) {
        auto it = plant_state_.find(src->addr);
        if (it != plant_state_.end()) pst = &it->second;
    }

    // ---- 植物侧增伤 ----
    double extra = 0.0;
    const char* tag = nullptr;
    if (src && pst) {
        double mult = 1.0, dr0 = 0.0;
        AuraFor(src->addr, mult, dr0);
        if (mult > 1.0) { extra += dmg * (mult - 1.0); tag = "鼓舞"; }
        if (pst->has(HEX_p_power))  { extra += dmg * tune::P_POWER; tag = "大力"; }
        if (pst->has(HEX_p_rageblade)) pst->rage_hits++;      // 攻速已走 +0x5C
        if (pst->has(HEX_p_zealot)) {
            std::uniform_real_distribution<double> u(0.0, 1.0);
            if (u(rng_) < tune::P_ZEALOT_DOUBLE) { extra += dmg; tag = "狂热者"; }
        }
        if (pst->has(HEX_p_farsight) && z.x < tune::P_FARSIGHT_RANGE) {
            extra += dmg * tune::P_FARSIGHT; tag = "远射天资";
        }
        if (pst->has(HEX_p_bigpea)) { extra += dmg * tune::P_BIGPEA_DMG; tag = "大腕"; }
        if (pst->has(HEX_p_giantkiller)) {
            double ratio = (st.hp_max / 10.0) * tune::P_GIANTKILLER_PER_10HP;
            if (ratio > tune::P_GIANTKILLER_CAP) ratio = tune::P_GIANTKILLER_CAP;
            extra += dmg * ratio;
            if (!tag) tag = "巨人杀手";
        }
    }

    double net = dmg + extra;

    // ---- 僵尸侧免伤 ----
    double dr = 0.0;
    if (st.has(HEX_z_lone) && Lonely(z)) dr = std::max(dr, tune::Z_LONE_DR);
    if (st.has(HEX_z_titanresolve) && st.resolve_stacks > 0) {
        double tr = st.resolve_stacks * tune::Z_TITANRESOLVE_PER_HIT;
        if (tr > tune::Z_TITANRESOLVE_CAP) tr = tune::Z_TITANRESOLVE_CAP;
        dr = std::max(dr, tr);
    }
    // 死之舞：40% 转成 3 秒流血
    if (st.has(HEX_z_deathdance) && net > 0) {
        double deferred = net * tune::Z_DEATHDANCE_RATIO;
        net -= deferred;
        st.dot_rate += deferred / (tune::Z_DEATHDANCE_TIME / 1000.0);
        st.dot_until_ms = now_ms_ + tune::Z_DEATHDANCE_TIME;
    }
    net *= (1.0 - dr);

    if (std::fabs(net - dmg) > 0.5) {
        int want = hp + (int)lround(dmg - net);
        want = Clamp(want, 0, st.hp_max);
        Wr<int>(z.addr + Z_HP, want);
        st.hp_prev = want;
        Obj* o = FindZombie(z.addr);
        if (o) o->hp = want;
        hp = want;
    }

    // 泰坦的坚决：这次受伤让它更硬（下一次开始生效）
    if (st.has(HEX_z_titanresolve)) {
        int cap = (int)lround(tune::Z_TITANRESOLVE_CAP / tune::Z_TITANRESOLVE_PER_HIT);
        if (st.resolve_stacks < cap) st.resolve_stacks++;
    }

    // 豪猪：反弹给打它的那株植物
    if (st.has(HEX_z_porcupine) && src && dmg > 0) {
        auto it = plant_state_.find(src->addr);
        if (it != plant_state_.end()) {
            Obj* p = FindPlant(src->addr);
            if (p) DamagePlant(*p, it->second, (int)lround(dmg * tune::Z_PORCUPINE));
        }
    }

    // 冰寒：减速植物命中 → 在原生减速之上再压一段（终极不可阻挡免疫）
    if (src && pst && IsSlowPlant(src->type) && pst->has(HEX_p_chill) &&
        !st.has(HEX_z_legday) && !CcImmune(st)) {
        st.chill_until_ms = now_ms_ + tune::P_CHILL_TIME;
        st.chill_ratio = tune::P_CHILL_EXTRA;
    }

    if (src && pst) PlantAfterHit(*src, *pst, z, dmg);

    // 击杀结算
    if (hp <= 0 && !st.fired.count("killed")) {
        st.fired.insert("killed");
        if (st.has(HEX_z_clowncollege) && !st.fired.count("clown")) {
            st.fired.insert("clown");
            ClownBurst(z);
        }
        if (pst && pst->has(HEX_p_collector)) AddSun(tune::P_COLLECTOR_SUN);
    }
    return hp;
}

// ---------------------------------------------------------------------------
//  命中之后的附带效果
// ---------------------------------------------------------------------------
void Engine::PlantAfterHit(const Obj& p, ObjState& pst, const Obj& z, int dmg) {
    // 大腕：整格击退
    if (pst.has(HEX_p_bigpea)) KnockCell(z, tune::P_BIGPEA_KNOCK);

    // 花晓之剑：按伤害比例回复已损失生命
    if (pst.has(HEX_p_bloodsword)) {
        int cur = RdFast<int>(p.addr + P_HP);
        int lost = pst.hp_max - cur;
        if (lost > 0) {
            int heal = (int)lround(dmg * tune::P_BLOODSWORD_RATIO);
            if (heal > lost) heal = lost;
            if (heal >= 1) {
                int nv = cur + heal;
                Wr<int>(p.addr + P_HP, nv);
                pst.hp_prev = nv;
                Obj* o = FindPlant(p.addr);
                if (o) o->hp = nv;
            }
        }
    }

    // 电龙魂：周围僵尸溅射（5 秒冷却）
    if (pst.has(HEX_p_storm) &&
        (pst.last_aoe_ms == 0 || now_ms_ - pst.last_aoe_ms >= tune::P_STORM_CD)) {
        pst.last_aoe_ms = now_ms_;
        int splash = (int)lround(std::max(1.0, dmg * tune::P_STORM_DMG));
        for (Obj& o : zombies_) {
            if (o.addr == z.addr) continue;
            if (std::abs(o.x - z.x) > tune::P_STORM_RADIUS) continue;
            if (std::abs(o.y - z.y) > tune::P_STORM_RADIUS) continue;
            auto it = zombie_state_.find(o.addr);
            if (it != zombie_state_.end()) DamageZombie(o, it->second, splash);
        }
    }
}

// ---------------------------------------------------------------------------
//  僵尸打植物这一下
// ---------------------------------------------------------------------------
void Engine::ZombieAttackPlant(const Obj& z, ObjState& zst, Obj& p, ObjState& pst,
                               int dmg, bool baron) {
    zst.last_bite_ms = now_ms_;

    double extra = 0.0;
    if (zst.has(HEX_z_swift))  extra += dmg * tune::Z_SWIFT_SPEED;
    if (zst.has(HEX_z_zealot)) {
        extra += dmg * tune::Z_ZEALOT_SPEED;
        std::uniform_real_distribution<double> u(0.0, 1.0);
        if (u(rng_) < tune::Z_ZEALOT_DOUBLE) extra += dmg;
    }
    if (zst.has(HEX_z_lone) && Lonely(z)) extra += dmg * tune::Z_LONE_ATKSPD;
    if (zst.has(HEX_z_unyielding)) {
        double lost = 1.0 - (double)z.hp / (zst.hp_max ? zst.hp_max : 1);
        if (lost < 0) lost = 0;
        extra += dmg * tune::Z_UNYIELDING_ATKSPD * lost;
        if (lost > 0) HealZombie(z, zst, (int)lround(dmg * tune::Z_UNYIELDING_LIFESTEAL * lost));
    }
    if (zst.has(HEX_z_goliath))     extra += dmg * tune::Z_GOLIATH_DMG;
    if (zst.has(HEX_z_smallkiller)) extra += dmg * tune::Z_SMALLKILLER_DMG;
    if (baron)                      extra += dmg * tune::Z_BARON_DMG;
    if (zst.has(HEX_z_heavy))       extra += zst.hp_max * tune::Z_HEAVY_RATIO;
    if (zst.has(HEX_z_astral))      extra -= dmg * tune::Z_ASTRAL_DMG;
    if (zst.has(HEX_z_bloodthirst)) HealZombie(z, zst, (int)lround(dmg * tune::Z_BLOODTHIRST));

    if (extra > 0.5) DamagePlant(p, pst, (int)lround(extra));

    // 装甲冲击：第一次进攻按护甲+护盾来一发范围伤害
    if (zst.has(HEX_z_armorcrash) && !zst.fired.count("armorcrash")) {
        zst.fired.insert("armorcrash");
        int base = RdFast<int>(z.addr + Z_HELMET) + RdFast<int>(z.addr + Z_SHIELD);
        int burst = (int)lround(base * tune::Z_ARMORCRASH_RATIO);
        if (burst > 1) {
            for (Obj& o : plants_) {
                if (std::abs(o.y - p.y) >= tune::ROW_TOL) continue;
                if (std::abs(o.x - p.x) > tune::Z_ARMORCRASH_RANGE) continue;
                auto it = plant_state_.find(o.addr);
                if (it != plant_state_.end()) DamagePlant(o, it->second, burst);
            }
        }
    }

    // 邦！：每次攻击都带范围伤害
    if (zst.has(HEX_z_bang)) {
        int splash = (int)lround(dmg * tune::Z_BANG_RATIO);
        if (splash > 0) {
            for (Obj& o : plants_) {
                if (o.addr == p.addr) continue;
                if (std::abs(o.y - p.y) >= tune::ROW_TOL) continue;
                if (std::abs(o.x - p.x) > tune::Z_BANG_RANGE) continue;
                auto it = plant_state_.find(o.addr);
                if (it != plant_state_.end()) DamagePlant(o, it->second, splash);
            }
        }
    }
}

// ---------------------------------------------------------------------------
//  植物挨打
// ---------------------------------------------------------------------------
void Engine::OnPlantDamaged(Obj& p, ObjState& st, int dmg, bool baron) {
    // 会心防御：25% 概率完全无视
    if (st.has(HEX_p_critdef)) {
        std::uniform_real_distribution<double> u(0.0, 1.0);
        if (u(rng_) < tune::P_CRITDEF) {
            int restore = RdFast<int>(p.addr + P_HP) + dmg;
            Wr<int>(p.addr + P_HP, restore);
            st.hp_prev = restore;
            p.hp = restore;
            return;
        }
    }

    double mult = 1.0, dr = 0.0;
    AuraFor(p.addr, mult, dr);
    double net = dmg * (1.0 - dr);

    // 山脉龙魂：护盾先顶
    if (st.has(HEX_p_mountain) && st.shield > 0) {
        double absorb = std::min((double)st.shield, net);
        st.shield -= (int)lround(absorb);
        net -= absorb;
    }

    int apply = (int)lround(net);
    if (apply > 0) {
        int cur = RdFast<int>(p.addr + P_HP);
        int nv = cur - apply;
        if (nv < 0) nv = 0;
        Wr<int>(p.addr + P_HP, nv);
        st.hp_prev = nv;
        p.hp = nv;
    }

    // 泰坦的坚决：挨打变硬
    if (st.has(HEX_p_titanresolve)) {
        int cap = (int)lround(tune::P_TITANRESOLVE_CAP / tune::P_TITANRESOLVE_PER_HIT);
        if (st.resolve_stacks < cap) st.resolve_stacks++;
    }

    // 荆棘之甲 + 僵尸啃食的附加伤害，都归给「谁在啃」
    Obj* eater = AttributeEater(p);
    if (eater) {
        auto it = zombie_state_.find(eater->addr);
        if (it != zombie_state_.end()) {
            if (st.has(HEX_p_thornmail)) {
                DamageZombie(*eater, it->second, (int)lround(dmg * tune::P_THORNMAIL));
            }
            ZombieAttackPlant(*eater, it->second, p, st, dmg, baron);
        }
    }
}

}  // namespace hx
