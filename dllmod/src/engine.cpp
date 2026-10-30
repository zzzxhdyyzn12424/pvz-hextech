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
void Engine::ApplyPlantFrame(const Obj& p, ObjState& st) {
    double mult = 1.0;
    if (st.has(HEX_p_swift))  mult += tune::P_SWIFT_SPEED;
    if (st.has(HEX_p_zealot)) mult += tune::P_ZEALOT_SPEED;
    if (st.has(HEX_p_rageblade)) {
        double rb = st.rage_hits * tune::P_RAGEBLADE_PER_HIT;
        if (rb > tune::P_RAGEBLADE_CAP) rb = tune::P_RAGEBLADE_CAP;
        mult += rb;
    }
    // 大腕：攻速 -87.5% → 间隔 ×8（mult 可以小于 1）
    if (st.has(HEX_p_bigpea)) mult *= (1.0 - tune::P_BIGPEA_SLOW);
    if (std::fabs(mult - 1.0) < 1e-9) return;

    uintptr_t a = p.addr + P_SHOOT_INT;
    int cur = RdFast<int>(a);
    if (st.shoot_base == 0) {
        if (!(cur > 1 && cur < 100000)) return;   // 不是射手 / 字段还没初始化
        st.shoot_base = NativeShootBase(cur, mult);
    }
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

bool Engine::Lonely(const Obj& z) const {
    for (const Obj& o : zombies_) {
        if (o.addr == z.addr) continue;
        if (std::abs(o.x - z.x) < tune::Z_LONE_RANGE_X &&
            std::abs(o.y - z.y) < tune::Z_LONE_RANGE_Y) return false;
    }
    return true;
}

void Engine::ApplyZombieFrame(const Obj& z, ObjState& st, bool baron) {
    const uintptr_t a = z.addr;

    // ---------- 体型缩小：科学狂人 / 小小杀手 ----------
    // 改的是 GameObject 宽高（碰撞盒），**视觉大小不变** ——
    // PVZ 的绘制尺寸由 reanim 动画自带，对象里没有可直接写的缩放字段（已扫过）。
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
            // 每帧维护而不是只写一次：游戏在某些状态会重算宽高
            if (RdFast<int>(a + O_W) != tw) {
                Wr<int>(a + O_W, tw);
                Wr<int>(a + O_H, th);
            }
        }
    }

    // ---------- 血量记账：这一帧掉血了没有 ----------
    // 没有伤害钩子的时候，「掉血」就是唯一的受伤信号。用它代替事件。
    int hp = RdFast<int>(a + Z_HP);
    if (hp < st.hp_prev) {
        st.hits++;
        st.last_hit_ms = now_ms_;
    }
    st.hp_prev = hp;

    // 护盾被打也算受伤（否则「脱战回盾」会变成每帧回满 = 无敌）
    int cur_shield = RdFast<int>(a + Z_SHIELD);
    if (cur_shield < st.shield) st.last_hit_ms = now_ms_;
    st.shield = cur_shield;
    if (cur_shield > 0) stats_shield_++;

    // ---------- 山脉龙魂：原生护盾 + 脱战回满 ----------
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

    // ---------- 逃跑计划：血量降到 50% 触发一次 ----------
    if (st.has(HEX_z_escape) && !st.fired.count("escape")) {
        if (hp > 0 && hp <= (int)(st.hp_max * tune::Z_ESCAPE_RATIO)) {
            st.fired.insert("escape");
            st.escape_ms = now_ms_;
            st.escape_tick_ms = now_ms_;
            int sm = (int)(st.hp_max * tune::Z_ESCAPE_SHIELD);
            if (sm > st.shield_max) st.shield_max = sm;
            st.shield = st.shield_max;
            ApplyShield(a, st.shield, st.shield_max, true);
            Log("[hex] zombie@%p 逃跑计划：护盾 %d，移速 ×3 开始衰减",
                (void*)a, st.shield_max);
        }
    }

    // ---------- 逃跑计划：护盾 5 秒内衰减到 0 ----------
    if (st.fired.count("escape") && st.shield_max > 0) {
        // 按**真实流逝的毫秒**扣，不按调用次数 —— 这个 thunk 每秒被调多少次
        // 并不固定（日志里 2 秒内跳 95~800 次），按次数扣会快好几倍。
        // 用减法而不是「按剩余时间重算」，是为了让已经被打掉的护盾不回弹。
        uint32_t dt = now_ms_ - st.escape_tick_ms;
        if (dt > 100) dt = 100;          // 卡顿/暂停时别一次扣爆
        st.escape_tick_ms = now_ms_;
        int dec = (int)(st.shield_max * (double)dt
                        / (double)tune::kEscapeDecayMs + 0.5);
        int cur = RdFast<int>(a + Z_SHIELD);
        if (cur > 0 && dec > 0) {
            int nv = cur - dec;
            if (nv < 0) nv = 0;
            Wr<int>(a + Z_SHIELD, nv);
            st.shield = nv;
        }
    }

    // ---------- 移速：补位移，不写 +0x34 ----------
    //
    // ★ 为什么不写 Zombie+0x34（外置版逆向 + 实测结论，别改回去）：
    //   Zombie::Update 的移动段在 0x0052947B：
    //       fld  [esi+0x34]        ; 当前速度
    //       fadd [0x679530]        ; + 0.01（斜坡步长）
    //       fld  [esp+8]           ; 目标速度（本帧重算）
    //       fcom st(1) / fstp st(1); 高于目标 → 直接取目标
    //       fst  [esi+0x34]        ; 写回
    //       fstp [esi+0x2c]        ; posX += 速度×系数
    //   每帧都做 speed = min(speed + 0.01, target)，target 现算。
    //   实测对比（同一只僵尸，按游戏秒归一化）：
    //       基准 ×1      19.5 px/游戏秒
    //       +0x34 ×2     20.2 px/游戏秒   ← 没变
    //       +0x34 ×3     12.3 px/游戏秒   ← 更慢（夹回过程占了斜坡）
    //   （我一开始在进程内测「写入 +0x34 三秒后还在」，以为不会被覆盖 ——
    //    那只是说明游戏没在那几帧重算这个僵尸的速度，不代表它真的走快了。
    //    要判定有没有效，只能看 px/秒，不能看字段。）
    //
    //   +0x2C 才是真正的位置（float），写进去游戏完全接受、不会回弹。
    //   所以移速加成 = 每帧「观测它自己走了多少，再按倍率补一份回去」。
    //   进程内跑在 Board::Update 开头，每帧一次，比外置版按 30Hz 采样更准。
    double bonus = 0.0;
    if (st.has(HEX_z_lone) && Lonely(z)) bonus += tune::Z_LONE_SPEED;
    // 家园卫士：还没挨过打 → 移速翻倍。用 hits==0 判定，
    // 等价于「血量从没掉过」，比读某个标志位可靠。
    if (st.has(HEX_z_homeguard) && st.hits == 0) bonus += tune::Z_HOMEGUARD_SPEED;
    if (st.has(HEX_z_legday))      bonus += tune::Z_LEGDAY_SPEED;
    if (st.has(HEX_z_madman))      bonus += tune::Z_MADMAN_SPEED;
    if (st.has(HEX_z_smallkiller)) bonus += tune::Z_SMALLKILLER_SPEED;
    if (baron)                     bonus += tune::Z_BARON_SPEED;   // 光环，含自己
    if (st.fired.count("escape")) {
        uint32_t el = now_ms_ - st.escape_ms;
        double left = (el >= tune::kEscapeDecayMs)
                      ? 0.0 : 1.0 - (double)el / tune::kEscapeDecayMs;
        bonus += tune::Z_ESCAPE_SPEED * left;
    }
    if (bonus <= 0.0) {
        st.fx_ok = false;   // 清掉基准，下次有加成时重新对齐，避免算出天文数字
        return;
    }

    float fx = RdFast<float>(a + O_FX);
    if (!(fx > -100000.0f && fx < 100000.0f)) return;
    if (!st.fx_ok) {
        st.prev_fx = fx;      // 第一次见到：先记基准，这一帧不补
        st.fx_ok = true;
        return;
    }
    // prev_fx 是「我们上一帧补完之后」的坐标，所以这一段差值正好是
    // 游戏自己在这一帧里走的距离，不含我们补的部分。
    double own = (double)fx - (double)st.prev_fx;
    double add = own * bonus;
    if (add >  tune::Z_NUDGE_MAX_PER_FRAME) add =  tune::Z_NUDGE_MAX_PER_FRAME;
    if (add < -tune::Z_NUDGE_MAX_PER_FRAME) add = -tune::Z_NUDGE_MAX_PER_FRAME;
    float nv = fx + (float)add;
    Wr<float>(a + O_FX, nv);
    st.prev_fx = nv;
    stats_speed_++;
    stats_nudge_ += add;
    st.sum_own += own;
    st.sum_add += add;
    // 每 0.5 秒采一次快照给日志线程看，别每帧都动 vector
    if (now_ms_ - last_spd_ms_ < 500 && spd_rows_.size() < 6) {
        SpdRow r;
        r.idx = z.idx;
        r.bonus = bonus;
        r.own = st.sum_own;
        r.add = st.sum_add;
        spd_rows_.push_back(r);
    }
}

void Engine::Tick() {
    ++frame_;
    now_ms_ = GetTickCount();    Scan();
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
    bool baron = false;
    for (const Obj& z : zombies_) {
        auto it = zombie_state_.find(z.addr);
        if (it != zombie_state_.end() && it->second.has(HEX_z_baron)) {
            baron = true;
            break;
        }
    }

    for (const Obj& p : plants_) {
        auto it = plant_state_.find(p.addr);
        if (it != plant_state_.end()) ApplyPlantFrame(p, it->second);
    }
    for (const Obj& z : zombies_) {
        auto it = zombie_state_.find(z.addr);
        if (it != zombie_state_.end()) ApplyZombieFrame(z, it->second, baron);
    }

    // 面板快照：4Hz 重建，别每帧拼字符串
    if (now_ms_ - last_ov_ms_ >= 250) {
        last_ov_ms_ = now_ms_;
        BuildOverlay();
    }
}

// ---------------------------------------------------------------------------
// 面板数据
//
// 字符串在**游戏线程**里一次性拼好（这里），面板线程只读快照 ——
// 两边不共享可变状态，也不用担心面板线程读到半更新的 map。
// ---------------------------------------------------------------------------
void Engine::BuildOverlay() {
    std::vector<OverlayRow> rows;
    rows.reserve(plants_.size() + zombies_.size());

    auto add = [&](const Obj& o, std::map<uintptr_t, ObjState>& sm, bool plant) {
        auto it = sm.find(o.addr);
        if (it == sm.end()) return;
        const ObjState& st = it->second;

        OverlayRow r;
        r.plant  = plant;
        r.idx    = o.idx;
        r.type   = o.type;
        r.hp     = o.hp;
        r.hp_max = st.hp_max;
        r.shield = st.shield;

        const char* nm = "";
        if (plant) {
            if (o.type >= 0 && o.type < kPlantNamesCount) nm = kPlantNames[o.type];
        } else {
            if (o.type >= 0 && o.type < kZombieNamesCount) nm = kZombieNames[o.type];
        }
        if (nm && *nm) r.name = nm;
        else r.name = "类型" + std::to_string(o.type);
        r.shield = st.shield;

        for (int h : st.hexes) {
            if (!r.hexes.empty()) r.hexes += "·";
            r.hexes += kHexDefs[h].name;
        }
        if (r.hexes.empty()) r.hexes = "-";
        rows.push_back(std::move(r));
    };

    for (const Obj& p : plants_)  add(p, plant_state_,  true);
    for (const Obj& z : zombies_) add(z, zombie_state_, false);

    std::lock_guard<std::mutex> lk(ov_mx_);
    ov_rows_.swap(rows);
    ov_plants_   = (int)plants_.size();
    ov_zombies_  = (int)zombies_.size();
    ov_stamp_ms_ = now_ms_;
}

void Engine::CopyOverlay(std::vector<OverlayRow>& out, int& plants, int& zombies,
                         uint32_t& age_ms) const {
    std::lock_guard<std::mutex> lk(ov_mx_);
    out      = ov_rows_;
    plants   = ov_plants_;
    zombies  = ov_zombies_;
    uint32_t now = GetTickCount();
    age_ms = (ov_stamp_ms_ && now >= ov_stamp_ms_) ? (now - ov_stamp_ms_)
                                                   : 0xFFFFFFFFu;
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
