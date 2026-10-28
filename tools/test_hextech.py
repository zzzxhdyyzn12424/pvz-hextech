#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_hextech.py —— 离线回归测试：不需要游戏

用假 Game 模拟内存，验证：
    1. 波次数量规则 / 词条不可重复 / 适用性筛选
    2. 每条已实现词条的数值是否正确（确定性断言，不是"跑一遍不崩"）
    3. 对象增删、血量归零等边界情况

    python tools/test_hextech.py
"""

import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hextech_core as C

P_ADDR = 0x300000
P2_ADDR = 0x300000 + C.PLANT_STRIDE
Z_ADDR = 0x400000
Z2_ADDR = 0x400000 + C.ZOMBIE_STRIDE

FAILED = []
PASSED = 0


def check(name, got, want):
    global PASSED
    if got == want:
        PASSED += 1
        print("  OK  %-26s %s" % (name, got))
    else:
        FAILED.append(name)
        print("  XX  %-26s 得到 %s，期望 %s" % (name, got, want))


# ============================================================ 假 Game
class MockGame:
    def __init__(self):
        self.mem = {}
        self.pid = 0
        self.sun = 100
        self._wave = 1
        self.wave_total = 20
        self.level = 1
        self.plants = []
        self.zombies = []

    def _get(self, addr):
        return self.mem.get(addr, 0)

    def read(self, addr, n):
        return bytes(n)

    def u32(self, addr):
        return self._get(addr) & 0xFFFFFFFF

    def i32(self, addr):
        v = self._get(addr) & 0xFFFFFFFF
        return v - 0x100000000 if v & 0x80000000 else v

    def u8(self, addr):
        return self._get(addr) & 0xFF

    def write_i32(self, addr, value):
        self.mem[addr] = int(value) & 0xFFFFFFFF
        return True

    def write_u32(self, addr, value):
        return self.write_i32(addr, value)

    def write_u8(self, addr, value):
        self.mem[addr] = value & 0xFF
        return True

    # float 字段按「位模式」存进同一个 dict，和真实内存布局一致
    def f32(self, addr):
        return struct.unpack("<f", struct.pack(
            "<I", self._get(addr) & 0xFFFFFFFF))[0]

    def write_f32(self, addr, value):
        self.mem[addr] = struct.unpack("<I", struct.pack("<f", float(value)))[0]
        return True

    @property
    def gapp(self):
        return 0x100000

    @property
    def board(self):
        return 0x200000

    def sun(self):
        return self.sun

    def add_sun(self, d):
        self.sun += int(d)

    def wave(self):
        return self._wave

    def wave_total(self):
        return self.wave_total

    def level(self):
        return self.level

    def scan_plants(self):
        out = []
        for p in self.plants:
            p["x"] = self.i32(p["addr"] + C.O_X)
            p["y"] = self.i32(p["addr"] + C.O_Y)
            out.append(dict(p))
        return out

    def scan_zombies(self):
        # ★ 必须从内存回读坐标：移速 / 闪现是直接写 mX(+0x08) 的，
        #   不读回来就永远看不到位移。
        out = []
        for z in self.zombies:
            z["x"] = self.i32(z["addr"] + C.O_X)
            z["y"] = self.i32(z["addr"] + C.O_Y)
            out.append(dict(z))
        return out


def add_plant(g, ptype, row, col, hp, addr=P_ADDR):
    x, y = 40 + col * 80, 80 + row * 100
    g.mem[addr + C.P_HP] = hp
    g.mem[addr + C.O_X] = x
    g.mem[addr + C.O_Y] = y
    g.mem[addr + C.P_SHOOT_COUNTER] = 150
    g.mem[addr + C.P_SHOOT_INTERVAL] = 150   # 豌豆射手原生射击间隔（帧）
    g.plants.append(dict(addr=addr, kind="plant", idx=len(g.plants),
                         type=ptype, row=row, col=col, hp=hp,
                         x=x, y=y))


Z_BASE_SPEED = 0.23         # 普通僵尸的原生移速（常量表 0x679670）


def add_zombie(g, hp, x, y, addr=Z_ADDR, ztype=0, speed=Z_BASE_SPEED):
    g.mem[addr + C.Z_HP] = hp
    g.mem[addr + C.O_X] = x
    g.mem[addr + C.O_Y] = y
    g.mem[addr + C.O_W] = 50                    # 普通僵尸的碰撞盒（体型缩小用）
    g.mem[addr + C.O_H] = 110
    g.write_f32(addr + C.O_FX, float(x))       # 原生 float 坐标
    g.write_f32(addr + C.O_FY, float(y))
    g.write_f32(addr + C.Z_SPEED_FIELD, speed)  # 原生移速
    g.write_f32(addr + C.Z_ANIM_FIELD, 1.0)     # 动画速率（冰冻时会掉到 0.5）
    g.zombies.append(dict(addr=addr, kind="zombie", idx=len(g.zombies),
                          type=ztype, status=0, hp=hp,
                          helmet=0, helmet_max=0,
                          shield_type=0, shield=0, shield_max=0,
                          x=x, y=y, w=50, h=110))


def step(g, eng, n=1, dt=1.0 / 30.0, move=False, anim=None):
    """推进 n 帧，每帧把 mock 内存里的真实血量同步回快照。

    dt 固定成 1/30：引擎按墙钟算 dt，连续调用会落到 0.001 的下限，
    移速补位移这类「按 dt 累计」的逻辑就没法断言了。

    move=True 时模拟「游戏自己把僵尸往左推」：每帧
    posX -= (+0x34) × Z_SPEED_HZ × dt。移速词条是自适应的，
    没有这个「自身位移」当基准就测不出来。
    anim 不为 None 时按该倍率减速（模拟冰冻，正常是 1.0）。
    """
    k = 1.0 if anim is None else anim
    for _ in range(n):
        for z in g.zombies:
            z["hp"] = g.i32(z["addr"] + C.Z_HP)
        for p in g.plants:
            p["hp"] = g.i32(p["addr"] + C.P_HP)
        if move:
            for z in g.zombies:
                v = g.f32(z["addr"] + C.Z_SPEED_FIELD) or 0.0
                if 0 < v < 10:
                    fx = g.f32(z["addr"] + C.O_FX)
                    g.write_f32(z["addr"] + C.O_FX,
                                fx - v * C.Engine.Z_SPEED_HZ * k * dt)
        eng.last_tick = time.time() - dt
        eng.tick()


def setup(plant_type=0, plant_hp=300, z_hp=1000, z_x=300, p_hexes=(),
          z_hexes=(), two_zombies=False):
    """建一个干净的场景，并强制指定双方词条（绕开随机）。"""
    g = MockGame()
    add_plant(g, plant_type, 0, 0, plant_hp)
    add_zombie(g, z_hp, z_x, 80)
    if two_zombies:
        add_zombie(g, z_hp, z_x + 40, 80, addr=Z2_ADDR)
    eng = C.Engine(g, seed=1)
    eng.last_tick = 0.0
    step(g, eng, 2)
    eng.plant_state[P_ADDR].hexes = list(p_hexes)
    eng.zombie_state[Z_ADDR].hexes = list(z_hexes)
    if two_zombies:
        eng.zombie_state[Z2_ADDR].hexes = list(z_hexes)
    step(g, eng, 1)     # 让引擎把护盾之类的初始状态写进内存
    return g, eng


def damage_zombie(g, amount, addr=Z_ADDR):
    """模拟游戏 Zombie::TakeDamage（0x5317C0）：
        先扣护盾 +0xDC（护盾类型 +0xD8 非 0 才生效），溢出部分才扣身体 +0xC8。
    """
    if g.i32(addr + C.Z_SHIELD_TYPE) != 0:
        sh = g.i32(addr + C.Z_SHIELD)
        absorb = min(sh, amount)
        g.mem[addr + C.Z_SHIELD] = sh - absorb
        amount -= absorb
    if amount > 0:
        g.mem[addr + C.Z_HP] = max(0, g.i32(addr + C.Z_HP) - amount)


def damage_plant(g, amount):
    g.mem[P_ADDR + C.P_HP] = max(0, g.i32(P_ADDR + C.P_HP) - amount)


def add_sunflower(g, col, hexes, hp=300, addr=P2_ADDR):
    """加一株向日葵并指定它的词条（光环 / 替死用）。"""
    add_plant(g, 1, 0, col, hp, addr=addr)
    return addr


def set_armor(g, helmet=0, shield=0):
    """给第一只僵尸挂原生护甲 / 护盾（装甲冲击用）。"""
    g.mem[Z_ADDR + C.Z_HELMET] = helmet
    g.mem[Z_ADDR + C.Z_SHIELD] = shield
    g.mem[Z_ADDR + C.Z_SHIELD_TYPE] = 1 if shield else 0
    g.zombies[0]["helmet"] = helmet
    g.zombies[0]["helmet_max"] = helmet
    g.zombies[0]["shield"] = shield
    g.zombies[0]["shield_max"] = shield
    g.zombies[0]["shield_type"] = 1 if shield else 0


def zombie_lost(g, start, addr=Z_ADDR):
    return start - g.i32(addr + C.Z_HP)


def plant_lost(g, start):
    return start - g.i32(P_ADDR + C.P_HP)


# ============================================================ 测试
def t_rules():
    print("\n[1] 规则")
    # 当前采用「每个对象随机 1~3 个」
    seen = set()
    bad = 0
    for _ in range(600):
        n = C.roll_count()
        seen.add(n)
        if not (C.HEX_MIN <= n <= C.HEX_MAX):
            bad += 1
    check("随机数量越界次数", bad, 0)
    check("随机覆盖 1/2/3", sorted(seen), [1, 2, 3])

    # 波次公式留着备用
    for w, want in ((0, 1), (1, 2), (2, 3), (3, 3), (20, 3), (99, 3)):
        check("wave=%d 词条数" % w, C.hex_count_for_wave(w), want)

    bad = 0
    for _ in range(300):
        h = C.roll_hexes("plant", 3, 0)
        if len(h) != len(set(h)):
            bad += 1
        h = C.roll_hexes("zombie", 3)
        if len(h) != len(set(h)):
            bad += 1
    check("300 次抽样重复数", bad, 0)

    bad = 0
    for _ in range(300):
        for hid in C.roll_hexes("plant", 3, 3):     # 坚果
            if C.HEX_BY_ID[hid]["apply"] not in ("all", "tank"):
                bad += 1
    check("坚果抽到输出词条", bad, 0)

    bad = 0
    for _ in range(300):
        for hid in C.roll_hexes("plant", 3, 0):     # 豌豆射手
            if C.HEX_BY_ID[hid]["apply"] in ("tank", "slow"):
                bad += 1
    check("豌豆抽到承伤词条", bad, 0)

    has_chill = "p_chill" in [d["id"] for d in C.pool_for("plant", 5)]
    check("寒冰射手池含冰寒", has_chill, True)
    has_chill = "p_chill" in [d["id"] for d in C.pool_for("plant", 0)]
    check("豌豆池不含冰寒", has_chill, False)


def t_damage_boost():
    print("\n[2] 植物增伤（5 次 × 20 点）")
    for name, phex, zx, want in (
            ("无词条", (), 300, 100),
            ("大力 +35%", ("p_power",), 300, 135),
            ("远射（僵尸在 3 格）", ("p_farsight",), 300, 100),
            ("远射（僵尸在 1 格）", ("p_farsight",), 120, 150),
            ("大力 + 远射", ("p_power", "p_farsight"), 120, 185),
    ):
        g, eng = setup(z_x=zx, p_hexes=phex)
        for _ in range(5):
            damage_zombie(g, 20)
            step(g, eng, 2)
        check(name, zombie_lost(g, 1000), want)

    # 狂热者：25% 概率双倍，只能检查区间
    g, eng = setup(p_hexes=("p_zealot",))
    for _ in range(5):
        damage_zombie(g, 20)
        step(g, eng, 2)
    lost = zombie_lost(g, 1000)
    check("狂热者落在 100~200", 100 <= lost <= 200, True)


def t_zombie_defence():
    print("\n[3] 僵尸减伤（5 次 × 20 点）")
    for name, zhex, want in (
            ("无词条", (), 100),
            ("孤勇者（无邻居）", ("z_lone",), 50),
            ("死之舞（40% 转流血）", ("z_deathdance",), 60),
            ("山脉龙魂（30% 盾=300）", ("z_mountain",), 0),
            ("逃跑计划（未到 50% 血）", ("z_escape",), 100),
    ):
        g, eng = setup(z_hexes=zhex)
        for _ in range(5):
            damage_zombie(g, 20)
            step(g, eng, 2)
        check(name, zombie_lost(g, 1000), want)

    # 孤勇者有邻居时不该免伤
    g, eng = setup(z_hexes=("z_lone",), two_zombies=True)
    for _ in range(5):
        damage_zombie(g, 20)
        step(g, eng, 2)
    check("孤勇者（有邻居）", zombie_lost(g, 1000), 100)

    # 逃跑计划：50% 触发线（510 血不触发 / 500 血触发）
    for left, want in ((510, False), (500, True)):
        g, eng = setup(z_hp=1000, z_hexes=("z_escape",))
        g.mem[Z_ADDR + C.Z_HP] = left
        step(g, eng, 2)
        check("逃跑计划 @%d 血触发" % left,
              "escape" in eng.zombie_state[Z_ADDR].fired, want)
    g, eng = setup(z_hp=1000, z_hexes=("z_escape",))
    g.mem[Z_ADDR + C.Z_HP] = 500
    step(g, eng, 2)
    check("逃跑计划盾 = 最大血量 100%", eng.zombie_state[Z_ADDR].shield_max, 1000)

    # 山脉龙魂：3 秒未受伤才回盾
    g, eng = setup(z_hp=1000, z_hexes=("z_mountain",))
    st = eng.zombie_state[Z_ADDR]
    check("僵尸山脉龙魂初始盾", g.i32(Z_ADDR + C.Z_SHIELD), 300)
    damage_zombie(g, 200)
    step(g, eng, 1)
    check("护盾吸收到 100", g.i32(Z_ADDR + C.Z_SHIELD), 100)
    st.last_hit = time.time() - 2.0        # 才脱战 2 秒
    step(g, eng, 3)
    check("2 秒内不回盾", g.i32(Z_ADDR + C.Z_SHIELD), 100)
    st.last_hit = time.time() - 5.0        # 已超过 3 秒
    for _ in range(10):
        time.sleep(0.05)
        step(g, eng, 1)
    check("超过 3 秒开始回盾", g.i32(Z_ADDR + C.Z_SHIELD) > 100, True)


def t_zombie_offence():
    print("\n[4] 僵尸打植物（5 次 × 20 点）")
    for name, zhex, want_plant, want_z in (
            ("无词条", (), 100, 1000),
            ("重量级打手（+5% 上限=50）", ("z_heavy",), 350, 1000),
            ("渴血（回 30%，已满血）", ("z_bloodthirst",), 100, 1000),
    ):
        g, eng = setup(plant_hp=1000, z_x=60, z_hexes=zhex)
        for _ in range(5):
            damage_plant(g, 20)
            step(g, eng, 2)
        check(name + " 植物掉血", plant_lost(g, 1000), want_plant)
        check(name + " 僵尸血量", g.i32(Z_ADDR + C.Z_HP), want_z)

    # 渴血：僵尸受过伤之后应该回血
    g, eng = setup(plant_hp=1000, z_x=60, z_hexes=("z_bloodthirst",))
    g.mem[Z_ADDR + C.Z_HP] = 500
    step(g, eng, 1)
    before = g.i32(Z_ADDR + C.Z_HP)
    damage_plant(g, 100)
    step(g, eng, 2)
    check("渴血回了血", g.i32(Z_ADDR + C.Z_HP) > before - 100, True)


def t_plant_defence():
    print("\n[5] 植物减伤")
    g, eng = setup(plant_hp=1000, z_x=60, p_hexes=("p_critdef",))
    eng.P_CRITDEF = 1.0
    for _ in range(5):
        damage_plant(g, 20)
        step(g, eng, 2)
    check("会心防御 100% 免伤", plant_lost(g, 1000), 0)

    g, eng = setup(plant_hp=1000, z_x=60, p_hexes=("p_mountain",))
    step(g, eng, 1)
    st = eng.plant_state[P_ADDR]
    check("植物山脉龙魂初始盾", st.shield, 300)
    damage_plant(g, 200)
    step(g, eng, 2)
    check("护盾吸收后植物掉血", plant_lost(g, 1000), 0)


def t_special():
    print("\n[6] 特殊结算")
    # 升级献祭：给阳光
    g, eng = setup(plant_type=3, plant_hp=1000, z_x=120, p_hexes=("p_sacrifice",))
    eng.plant_state[P_ADDR].last_aoe = -99
    sun0 = g.sun
    step(g, eng, 2)
    check("升级献祭给阳光", g.sun - sun0, eng.P_SACRIFICE_SUN)
    check("升级献祭扣了僵尸血", zombie_lost(g, 1000) > 0, True)

    # 电龙魂：溅射到旁边的僵尸
    g, eng = setup(z_hexes=(), p_hexes=("p_storm",), two_zombies=True)
    damage_zombie(g, 20)
    step(g, eng, 2)
    check("电龙魂溅射到邻居", zombie_lost(g, 1000, Z2_ADDR) > 0, True)

    # 收集者：僵尸消失时给 50 阳光
    g, eng = setup(p_hexes=("p_collector",))
    damage_zombie(g, 20)
    step(g, eng, 1)
    sun0 = g.sun
    g.zombies.clear()          # 模拟僵尸被打死、从数组里消失
    step(g, eng, 1)
    check("收集者击杀奖励", g.sun - sun0, eng.P_COLLECTOR_SUN)


def t_edges():
    print("\n[7] 边界")
    g = MockGame()
    eng = C.Engine(g, seed=1)
    for i in range(6):
        g.zombies = []
        add_zombie(g, 200, 300, 80, addr=Z_ADDR + i * C.ZOMBIE_STRIDE)
        step(g, eng, 2)
    check("槽位复用不残留", len(eng.zombie_state), 1)

    g, eng = setup()
    damage_zombie(g, 99999)
    step(g, eng, 3)
    check("血量归零不崩", g.i32(Z_ADDR + C.Z_HP), 0)

    g = MockGame()
    eng = C.Engine(g, seed=1)
    g._wave = None
    eng.tick()
    check("没进关卡时对象为空", len(eng.objects), 0)


def t_new_hexes():
    print("\n[8] v1.0 新增词条")

    # ---- 互斥：山脉龙魂 与 逃跑计划 不能同时出现 ----
    bad = 0
    for seed in range(300):
        rng = __import__("random").Random(seed)
        for _ in range(4):
            hx = C.roll_hexes("zombie", 3, None, rng)
            if "z_mountain" in hx and "z_escape" in hx:
                bad += 1
    check("互斥：山/逃 从未同框", bad, 0)

    # ---- 互斥：四条移速词条不能同时出现 ----
    speed_ids = {"z_escape", "z_lone", "z_homeguard", "z_legday"}
    bad = 0
    seen = set()
    for seed in range(300):
        rng = __import__("random").Random(seed)
        for _ in range(4):
            hx = C.roll_hexes("zombie", 3, None, rng)
            hit = speed_ids & set(hx)
            if len(hit) > 1:
                bad += 1
            seen |= hit
    check("互斥：移速词条最多一条", bad, 0)
    check("互斥：四条移速词条都抽得到", len(seen), 4)

    # ---- 全心为你：向日葵替死，植物回到 100 血 ----
    g, eng = setup(plant_hp=300, z_x=200)
    add_sunflower(g, 0, [])
    step(g, eng, 1)
    eng.plant_state[P2_ADDR].hexes = ["p_devotion"]
    step(g, eng, 1)
    damage_plant(g, 300)
    step(g, eng, 2)
    check("全心为你：被救植物回到 100", g.i32(P_ADDR + C.P_HP), 100)
    check("全心为你：向日葵替死", g.i32(P2_ADDR + C.P_HP), 0)

    # ---- 鼓舞：周围植物 +20% 伤害（5 × 20 = 120）----
    g, eng = setup(z_x=300)
    add_sunflower(g, 1, [])
    step(g, eng, 1)
    eng.plant_state[P2_ADDR].hexes = ["p_inspire"]
    step(g, eng, 1)
    for _ in range(5):
        damage_zombie(g, 20)
        step(g, eng, 2)
    check("鼓舞：伤害 100 → 120", zombie_lost(g, 1000), 120)

    # ---- 风语者祝福：周围植物 25% 免伤（100 → 75）----
    g, eng = setup(plant_hp=1000, z_x=60)
    add_sunflower(g, 1, [])
    step(g, eng, 1)
    eng.plant_state[P2_ADDR].hexes = ["p_windbless"]
    step(g, eng, 1)
    for _ in range(5):
        damage_plant(g, 20)
        step(g, eng, 2)
    check("风语者祝福：掉血 100 → 75", plant_lost(g, 1000), 75)

    # ---- 守护：每 5 秒回 100 ----
    g, eng = setup(plant_hp=1000, z_x=400)
    add_sunflower(g, 1, [])
    step(g, eng, 1)
    eng.plant_state[P2_ADDR].hexes = ["p_guardian"]
    eng.plant_state[P2_ADDR].last_aoe = -99
    damage_plant(g, 100)
    step(g, eng, 2)
    check("守护：回 100 血", g.i32(P_ADDR + C.P_HP), 1000)

    # ---- 装甲冲击：第一次进攻，按 头盔+护盾 造成范围伤害 ----
    g, eng = setup(plant_hp=1000, z_x=60, z_hexes=("z_armorcrash",))
    set_armor(g, helmet=100, shield=200)
    damage_plant(g, 20)
    step(g, eng, 2)
    check("装甲冲击：20 + (100+200) = 320", plant_lost(g, 1000), 320)
    damage_plant(g, 20)
    step(g, eng, 2)
    check("装甲冲击：只触发一次", plant_lost(g, 1000), 340)

    # ---- 屹立不倒：损失 50% 血 → +100% 攻速 ----
    g, eng = setup(plant_hp=1000, z_x=60, z_hexes=("z_unyielding",))
    g.mem[Z_ADDR + C.Z_HP] = 500
    g.zombies[0]["hp"] = 500
    eng.zombie_state[Z_ADDR].hp_prev = 500
    step(g, eng, 1)
    z_hp0 = g.i32(Z_ADDR + C.Z_HP)
    damage_plant(g, 20)
    step(g, eng, 2)
    check("屹立不倒：20 → 40 伤害", plant_lost(g, 1000), 40)
    check("屹立不倒：吸血回了血", g.i32(Z_ADDR + C.Z_HP) > z_hp0, True)

    # ---- 星界躯体：+1800 血，造成伤害 -30% ----
    g, eng = setup(plant_hp=1000, z_x=60, z_hexes=("z_astral",))
    check("星界躯体：血量 1000 → 2800", g.i32(Z_ADDR + C.Z_HP), 2800)
    damage_plant(g, 20)
    step(g, eng, 2)
    check("星界躯体：20 → 14 伤害", plant_lost(g, 1000), 14)

    # ---- 邦！：每次攻击带 50% 范围伤害 ----
    g, eng = setup(plant_hp=1000, z_x=60, z_hexes=("z_bang",))
    add_plant(g, 0, 0, 1, 1000, addr=P2_ADDR)
    step(g, eng, 2)
    damage_plant(g, 20)
    step(g, eng, 2)
    check("邦！：主目标掉 20", plant_lost(g, 1000), 20)
    check("邦！：邻居掉 10", 1000 - g.i32(P2_ADDR + C.P_HP), 10)

    # ---- 豪猪：反弹 20% 所受伤害给攻击它的植物（20 → 4）----
    g, eng = setup(plant_hp=1000, z_x=300, z_hexes=("z_porcupine",))
    damage_zombie(g, 20)
    step(g, eng, 2)
    check("豪猪：反弹 4 点", plant_lost(g, 1000), 4)

    # ---- 闪现向前：越过植物，x 100 → 10 ----
    g, eng = setup(z_x=100, z_hexes=("z_flash",))
    check("闪现向前：越过后 x", g.i32(Z_ADDR + C.O_X), 10)
    check("闪现向前：剩 1 次", eng.zombie_state[Z_ADDR].flash_left, 1)

    # ---- 植物灵巧 / 狂热者 / 羊刀：缩短原生射击间隔 ----
    g, eng = setup(p_hexes=("p_swift",))
    check("植物灵巧：射击间隔 150 → 120",
          g.i32(P_ADDR + C.P_SHOOT_INTERVAL), 120)

    g, eng = setup(p_hexes=("p_zealot",))
    check("狂热者：射击间隔 150 → 130",
          g.i32(P_ADDR + C.P_SHOOT_INTERVAL), 130)

    g, eng = setup(p_hexes=("p_rageblade",))
    for _ in range(5):
        damage_zombie(g, 20)
        step(g, eng, 2)
    check("羊刀：命中 5 次叠到 5 层",
          eng.plant_state[P_ADDR].rage_hits, 5)
    check("羊刀：射击间隔 150 → 143",
          g.i32(P_ADDR + C.P_SHOOT_INTERVAL), 143)

    # 真的多射：模拟游戏 0x45F8A9 的倒计时，数重置次数
    def count_shots(phex, frames=1200, stacks=0):
        g, eng = setup(p_hexes=phex)
        eng.plant_state[P_ADDR].rage_hits = stacks
        step(g, eng, 1)
        g.mem[P_ADDR + C.P_SHOOT_COUNTER] = 1
        shots = 0
        for _ in range(frames):
            c = g.i32(P_ADDR + C.P_SHOOT_COUNTER) - 1
            if c <= 0:
                shots += 1
                c = g.i32(P_ADDR + C.P_SHOOT_INTERVAL)
            g.mem[P_ADDR + C.P_SHOOT_COUNTER] = c
            step(g, eng, 1)
        return shots

    base_shots = count_shots(())
    swift_shots = count_shots(("p_swift",))
    rage_shots = count_shots(("p_rageblade",), stacks=100)
    check("基础 1200 帧射 8 发", base_shots, 8)
    check("灵巧 1200 帧射 10 发", swift_shots, 10)
    check("羊刀满层 1200 帧射 16 发", rage_shots, 16)

    # ---- 僵尸灵巧：啃食速度 +25% ----
    g, eng = setup(plant_hp=1000, z_x=60, z_hexes=("z_swift",))
    for _ in range(5):
        damage_plant(g, 20)
        step(g, eng, 2)
    check("僵尸灵巧：5 次等效 125", plant_lost(g, 1000), 125)

    # ---- 万用瞄准镜：隔 1 格能啃，隔 2 格够不到 ----
    g, eng = setup(plant_hp=1000, z_x=200, z_hexes=("z_scope",))
    eng.zombie_state[Z_ADDR].scope_acc = 60
    step(g, eng, 1)
    check("万用瞄准镜：隔 1 格啃掉血", g.i32(P_ADDR + C.P_HP) < 1000, True)

    g, eng = setup(plant_hp=1000, z_x=250, z_hexes=("z_scope",))
    eng.zombie_state[Z_ADDR].scope_acc = 60
    step(g, eng, 2)
    check("万用瞄准镜：够不到 2 格", g.i32(P_ADDR + C.P_HP), 1000)

    # ---- 更万用瞄准镜：隔两格也能啃 ----
    g, eng = setup(plant_hp=1000, z_x=250, z_hexes=("z_scope2",))
    eng.zombie_state[Z_ADDR].scope_acc = 60
    step(g, eng, 1)
    check("更万用瞄准镜：隔格啃掉血", g.i32(P_ADDR + C.P_HP) < 1000, True)

    # ---- 冰寒：减速植物命中后，额外压掉 30% 位移 ----
    def chill_move(phex):
        g, eng = setup(plant_type=5, plant_hp=1000, z_x=300, p_hexes=phex)
        damage_zombie(g, 20)
        step(g, eng, 2)
        x0 = g.f32(Z_ADDR + C.O_FX)
        step(g, eng, 30, move=True)
        return x0 - g.f32(Z_ADDR + C.O_FX)

    plain = chill_move(())
    chilled = chill_move(("p_chill",))
    ratio = chilled / plain if plain else 0
    check("冰寒：额外减速到约 70%", 0.66 <= ratio <= 0.76, True)

    # 练腿日免疫冰寒
    def chill_move_legday():
        g, eng = setup(plant_type=5, plant_hp=1000, z_x=300,
                       p_hexes=("p_chill",), z_hexes=("z_legday",))
        damage_zombie(g, 20)
        step(g, eng, 2)
        x0 = g.f32(Z_ADDR + C.O_FX)
        step(g, eng, 30, move=True)
        return x0 - g.f32(Z_ADDR + C.O_FX)

    leg = chill_move_legday()
    check("冰寒：练腿日免疫额外减速", round(leg / plain, 1), 1.5)

    # ---- 移速加成：补位移方案（自适应）----
    def run_speed(z_hexes, z_x=400, ticks=30, warm=30, hp=None, anim=None):
        """跑一段，返回僵尸这 ticks 帧的总位移（px）。

        先 warm 帧让自适应收敛，再量。对照组用没有移速词条的 z_heavy。
        anim 不为 None 时把 +0x11C（动画速率）设成该值，并让 mock 按该比例
        减速，用来模拟「被冰冻」。
        """
        g, eng = setup(z_x=z_x, z_hexes=z_hexes)
        if hp is not None:
            g.mem[Z_ADDR + C.Z_HP] = hp
            g.zombies[0]["hp"] = hp
            eng.zombie_state[Z_ADDR].hp_prev = hp
        if anim is not None:
            g.write_f32(Z_ADDR + C.Z_ANIM_FIELD, anim)
        step(g, eng, warm, move=True, anim=anim)
        x0 = g.f32(Z_ADDR + C.O_FX)
        step(g, eng, ticks, move=True, anim=anim)
        return x0 - g.f32(Z_ADDR + C.O_FX)

    base_move = run_speed(("z_heavy",))
    esc_move = run_speed(("z_escape",), hp=500)
    lone_move = run_speed(("z_lone",))
    home_move = run_speed(("z_homeguard",))
    leg_move = run_speed(("z_legday",))

    check("基准：僵尸自己会走", base_move > 10, True)
    check("逃跑计划：移速 ≈ ×3", round(esc_move / base_move, 1), 3.0)
    check("孤勇者：移速 ≈ ×2", round(lone_move / base_move, 1), 2.0)
    check("家园卫士：没挨打时 ×2", round(home_move / base_move, 1), 2.0)
    check("练腿日：移速 ≈ ×1.5", round(leg_move / base_move, 1), 1.5)

    # 家园卫士：挨过一次打之后失效
    g, eng = setup(z_x=400, z_hexes=("z_homeguard",))
    step(g, eng, 10, move=True)
    damage_zombie(g, 30)                       # 模拟游戏扣血
    step(g, eng, 30, move=True)                # 让自适应重新收敛
    x0 = g.f32(Z_ADDR + C.O_FX)
    step(g, eng, 30, move=True)
    after = (x0 - g.f32(Z_ADDR + C.O_FX)) / base_move
    check("家园卫士：挨打后恢复原速", round(after, 1), 1.0)

    # 练腿日：免疫减速（被冰冻按 0.5 倍走，词条要把差额补回来）
    chilled_base = run_speed(("z_heavy",), anim=0.5)
    chilled_leg = run_speed(("z_legday",), anim=0.5)
    check("冰冻：基准确实变慢", chilled_base < base_move * 0.6, True)
    check("练腿日：免疫减速后 ≈ ×1.5",
          round(chilled_leg / chilled_base, 1), 3.0)

    # 没有移速词条时不该多补一个像素
    g, eng = setup(z_x=400, z_hexes=("z_heavy",))
    step(g, eng, 30, move=True)
    x0 = g.f32(Z_ADDR + C.O_FX)
    step(g, eng, 10, move=True)
    natural = Z_BASE_SPEED * C.Engine.Z_SPEED_HZ * (10 / 30.0)
    check("无移速词条：只有自然位移",
          round((x0 - g.f32(Z_ADDR + C.O_FX)) / natural, 2), 1.0)

    # 逃跑计划衰减后自动恢复原速
    g, eng = setup(z_x=400, z_hexes=("z_escape",))
    g.mem[Z_ADDR + C.Z_HP] = 500
    g.zombies[0]["hp"] = 500
    eng.zombie_state[Z_ADDR].hp_prev = 500
    step(g, eng, 20, move=True)
    check("逃跑计划：已触发", "escape" in eng.zombie_state[Z_ADDR].fired, True)
    eng.zombie_state[Z_ADDR].escape_t -= C.Engine.Z_ESCAPE_DECAY + 1.0
    step(g, eng, 5, move=True)
    x0 = g.f32(Z_ADDR + C.O_FX)
    step(g, eng, 10, move=True)
    back = (x0 - g.f32(Z_ADDR + C.O_FX)) / natural
    check("逃跑计划：衰减后恢复原速", round(back, 1), 1.0)

    # ---- 闪现向前：走 float 坐标 +0x2C，不是整数 mX ----
    g, eng = setup(z_x=100, z_hexes=("z_flash",))
    check("闪现向前：float 坐标前移 90",
          round(g.f32(Z_ADDR + C.O_FX), 1), 10.0)
    check("闪现向前：mX 同步跟上", g.i32(Z_ADDR + C.O_X), 10)


def t_probe_shoot():
    """probe_shoot 的开火计数逻辑（上机验证靠它，必须自己先对）。

    模拟 0x45F8A9 的倒计时行为，逐帧喂给 ShotCounter，
    验证它数出来的发数等于真实开火次数。
    """
    print("\n-- 上机探针：开火计数 --")
    import random
    import probe_shoot as PS

    def simulate(interval, frames, jitter=True):
        """返回 (每帧读数列表, 真实开火次数)。"""
        rnd = random.Random(1234)
        cnt = interval
        seq = []
        shots = 0
        for _ in range(frames):
            seq.append(cnt)
            cnt -= 1
            if cnt <= 0:
                j = rnd.randint(0, 15) if jitter else 0
                cnt = interval - j
                shots += 1
        return seq, shots

    for itv, frames in ((150, 1200), (120, 1200), (75, 1200), (143, 1200)):
        seq, want = simulate(itv, frames)
        c = PS.ShotCounter()
        for v in seq:
            c.feed(v)
        check("探针计数 %d 帧间隔/%d 帧" % (itv, frames), c.shots, want)

    # 抖动存在时也不能多算或少算
    seq, want = simulate(150, 3000, jitter=True)
    c = PS.ShotCounter()
    for v in seq:
        c.feed(v)
    check("探针计数：带随机抖动", c.shots, want)

    # 游戏暂停（读数一直不变）不该被算成开火
    c = PS.ShotCounter()
    for _ in range(200):
        c.feed(0)
    check("探针：读数卡在 0 不算开火", c.shots, 0)

    # 读数恒为大数（没在开火）也不该算
    c = PS.ShotCounter()
    for _ in range(200):
        c.feed(150)
    check("探针：读数恒定不算开火", c.shots, 0)

    # 间隔越短，同样帧数里开火越多 —— 这就是「真的多射」的直接证据
    _, w150 = simulate(150, 1200)
    _, w120 = simulate(120, 1200)
    check("间隔 150 -> 120 后开火变多", w120 > w150, True)


def t_new_hexes_v16():
    print("\n[9] v1.6 新增词条（植物 6 + 僵尸 5）")

    def run_speed(z_hexes, z_x=400, ticks=30, warm=30):
        g, eng = setup(z_x=z_x, z_hexes=z_hexes)
        step(g, eng, warm, move=True)
        x0 = g.f32(Z_ADDR + C.O_FX)
        step(g, eng, ticks, move=True)
        return x0 - g.f32(Z_ADDR + C.O_FX)

    base_move = run_speed(("z_heavy",))

    # ==================== 植物 ====================
    # ---- 大腕 ----
    g, eng = setup(p_hexes=("p_bigpea",))
    step(g, eng, 3)
    check("大腕：射击间隔 150 → 600", g.i32(P_ADDR + C.P_SHOOT_INTERVAL), 600)

    g, eng = setup(p_hexes=("p_bigpea",))
    for _ in range(5):
        damage_zombie(g, 20)
        step(g, eng, 2)
    check("大腕：5 次共 200 伤害", zombie_lost(g, 1000), 200)

    g, eng = setup(z_x=300, p_hexes=("p_bigpea",))
    x0 = g.i32(Z_ADDR + C.O_X)
    damage_zombie(g, 20)
    step(g, eng, 2)
    check("大腕：命中击退 80px", g.i32(Z_ADDR + C.O_X) - x0, 80)

    # ---- 花晓之剑 ----
    g, eng = setup(plant_hp=300, p_hexes=("p_bloodsword",))
    damage_plant(g, 100)
    step(g, eng, 2)
    check("花晓之剑：受伤后剩 200", g.i32(P_ADDR + C.P_HP), 200)
    damage_zombie(g, 100)
    step(g, eng, 2)
    check("花晓之剑：命中 100 回血 30", g.i32(P_ADDR + C.P_HP), 230)

    # ---- 泰坦的坚决 ----
    g, eng = setup(plant_hp=300, p_hexes=("p_titanresolve",))
    damage_plant(g, 100)
    step(g, eng, 2)
    check("泰坦的坚决：第一次不减伤", g.i32(P_ADDR + C.P_HP), 200)
    check("泰坦的坚决：叠到 1 层",
          eng.plant_state[P_ADDR].resolve_stacks, 1)
    damage_plant(g, 100)
    step(g, eng, 2)
    check("泰坦的坚决：第二次减伤 5%", g.i32(P_ADDR + C.P_HP), 105)
    check("泰坦的坚决：叠到 2 层",
          eng.plant_state[P_ADDR].resolve_stacks, 2)

    # ---- 荆棘之甲 ----
    g, eng = setup(plant_hp=300, z_x=100, p_hexes=("p_thornmail",))
    damage_plant(g, 100)
    step(g, eng, 2)
    check("荆棘之甲：反弹 50", zombie_lost(g, 1000), 50)

    # ---- 泰坦巨人 ----
    g, eng = setup(plant_hp=300, p_hexes=("p_titan",))
    step(g, eng, 3)
    check("泰坦巨人：上限 300 → 900", g.i32(P_ADDR + C.P_HP_MAX), 900)
    check("泰坦巨人：当前血补到 900", g.i32(P_ADDR + C.P_HP), 900)

    g, eng = setup()
    st = eng.plant_state[P_ADDR]
    st.hexes = ["p_titan"]
    st.titan_sun_done = False
    sun0 = g.sun
    eng._titan_charge({"addr": P_ADDR, "type": 0}, st)
    check("泰坦巨人：额外扣 100 阳光", sun0 - g.sun, 100)
    # 同一次种植只扣一次
    eng._titan_charge({"addr": P_ADDR, "type": 0}, st)
    check("泰坦巨人：只扣一次", sun0 - g.sun, 100)

    # ---- 巨人杀手 ----
    g, eng = setup(z_hp=1000, p_hexes=("p_giantkiller",))
    for _ in range(5):
        damage_zombie(g, 20)
        step(g, eng, 2)
    check("巨人杀手：1000 血 +100%", zombie_lost(g, 1000), 200)

    g, eng = setup(z_hp=200, p_hexes=("p_giantkiller",))
    for _ in range(5):
        damage_zombie(g, 20)
        step(g, eng, 2)
    check("巨人杀手：200 血 +20%", zombie_lost(g, 200), 120)

    # ==================== 僵尸 ====================
    # ---- 歌莉娅巨人 ----
    g, eng = setup(z_hp=1000, z_hexes=("z_goliath",))
    step(g, eng, 3)
    check("歌莉娅巨人：血量 1000 → 2000", g.i32(Z_ADDR + C.Z_HP), 2000)

    g, eng = setup(plant_hp=300, z_x=100, z_hexes=("z_goliath",))
    damage_plant(g, 100)
    step(g, eng, 2)
    check("歌莉娅巨人：植物多掉 50", g.i32(P_ADDR + C.P_HP), 150)

    # ---- 科学狂人 ----
    mad = run_speed(("z_madman",))
    check("科学狂人：移速 ≈ ×2.5", round(mad / base_move, 1), 2.5)

    g, eng = setup(z_hexes=("z_madman",))
    step(g, eng, 3)
    check("科学狂人：宽 50 → 30", g.i32(Z_ADDR + C.O_W), 30)
    check("科学狂人：高 110 → 66", g.i32(Z_ADDR + C.O_H), 66)

    # ---- 小小杀手 ----
    sk = run_speed(("z_smallkiller",))
    check("小小杀手：移速 ≈ ×2", round(sk / base_move, 1), 2.0)

    g, eng = setup(plant_hp=300, z_x=100, z_hexes=("z_smallkiller",))
    damage_plant(g, 100)
    step(g, eng, 2)
    check("小小杀手：植物多掉 50", g.i32(P_ADDR + C.P_HP), 150)

    # ---- 小丑学院 ----
    g, eng = setup(plant_hp=300, z_x=100, z_hexes=("z_clowncollege",))
    damage_zombie(g, 1000)
    step(g, eng, 2)
    check("小丑学院：植物被炸掉 180", g.i32(P_ADDR + C.P_HP), 120)
    check("小丑学院：只炸一次",
          "clown" in eng.zombie_state[Z_ADDR].fired, True)

    # ---- 男爵之手 ----
    baron = run_speed(("z_baron",))
    check("男爵之手：自身移速 ≈ ×1.5", round(baron / base_move, 1), 1.5)

    g, eng = setup(plant_hp=300, z_x=100, z_hexes=("z_baron",))
    damage_plant(g, 100)
    step(g, eng, 2)
    check("男爵之手：自身伤害 +50%", g.i32(P_ADDR + C.P_HP), 150)

    # 光环：场上有男爵时，没带这词条的僵尸也加速
    g, eng = setup(z_x=400, z_hexes=("z_heavy",))
    step(g, eng, 30, move=True)
    x0 = g.f32(Z_ADDR + C.O_FX)
    step(g, eng, 30, move=True)
    plain = x0 - g.f32(Z_ADDR + C.O_FX)

    g, eng = setup(z_x=400, z_hexes=("z_baron",), two_zombies=True)
    eng.zombie_state[Z2_ADDR].hexes = ["z_heavy"]     # 第二只没带男爵
    step(g, eng, 30, move=True)
    x0 = g.f32(Z2_ADDR + C.O_FX)
    step(g, eng, 30, move=True)
    other = x0 - g.f32(Z2_ADDR + C.O_FX)
    check("男爵之手：光环让别的僵尸 ×1.5", round(other / plain, 1), 1.5)

    # ==================== 规则 ====================
    bad = 0
    for seed in range(300):
        rng = __import__("random").Random(seed)
        for _ in range(4):
            hx = C.roll_hexes("zombie", 3, None, rng)
            if "z_madman" in hx and "z_smallkiller" in hx:
                bad += 1
    check("互斥：科学狂人/小小杀手 从未同框", bad, 0)

    want_plant = {"p_bigpea", "p_bloodsword", "p_titanresolve",
                  "p_thornmail", "p_titan", "p_giantkiller"}
    seen = set()
    for seed in range(300):
        rng = __import__("random").Random(seed)
        for pt in (0, 3, 5):
            seen |= set(C.roll_hexes("plant", 3, pt, rng))
    check("植物新词条都能抽到", len(want_plant & seen), 6)

    want_z = {"z_goliath", "z_madman", "z_clowncollege",
              "z_smallkiller", "z_baron"}
    seen = set()
    for seed in range(300):
        rng = __import__("random").Random(seed)
        seen |= set(C.roll_hexes("zombie", 3, None, rng))
    check("僵尸新词条都能抽到", len(want_z & seen), 5)


def main():
    t_rules()
    t_damage_boost()
    t_zombie_defence()
    t_zombie_offence()
    t_plant_defence()
    t_special()
    t_edges()
    t_new_hexes()
    t_probe_shoot()
    t_new_hexes_v16()

    print("\n" + "=" * 52)
    if FAILED:
        print("失败 %d 项：%s" % (len(FAILED), FAILED))
        print("通过 %d 项" % PASSED)
        return 1
    print("全部 %d 项通过" % PASSED)
    return 0


if __name__ == "__main__":
    sys.exit(main())
