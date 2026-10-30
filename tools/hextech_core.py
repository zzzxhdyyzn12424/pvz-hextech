#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hextech_core.py —— 植物大战僵尸「海克斯版」核心引擎（无界面）

拆成两层：
    hextech_core.py   ← 本文件：内存访问 / 对象扫描 / 词条定义 / 效果结算
    hextech.py        ← 入口：命令行 + 透明覆盖窗口

--------------------------------------------------------------------------
一、内存地基（全部静态反汇编 + 版本表交叉核对，见 docs/hextech_design.md）
--------------------------------------------------------------------------
    gLawnApp          = [0x006A9EC0]
    Board             = [LawnApp + 0x768]

    Board + 0x090 = 僵尸数组基址（指向堆上连续对象）
    Board + 0x094 = 僵尸数组容量
    Board + 0x0AC = 植物数组基址
    Board + 0x0B0 = 植物数组容量
    Board + 0x5550 = 关卡号（1 起）
    Board + 0x5560 = 阳光
    Board + 0x5564 = 本关总波数
    Board + 0x5568 = ❓ 还没吃准（实测读到 6725，不是波次，先不用）
    Board + 0x554C = 场景

    sizeof(Plant)  = 0x14C   (0x41DE9B  imul eax, eax, 0x14c)
    sizeof(Zombie) = 0x15C   (0x41BB64  add edi, 0x15c)

    对象公共头（本地实测）：
        +0x00 gApp  +0x04 Board  +0x08 mX(int)  +0x0C mY(int)
    植物：+0x1C row  +0x24 type  +0x28 col  +0x40 hp  +0x141 dead
    僵尸：+0x24 type +0x28 status +0xC8 hp +0xD0 盔甲 +0xDC 护盾 +0xEC dead

--------------------------------------------------------------------------
二、架构：用「血量通道」代替代码钩子
--------------------------------------------------------------------------
    每帧读所有对象血量 → 与上一帧比较 → 少了 D 就是「刚被打了 D 点」
    → 这是唯一的战斗事件来源。
    想加伤害就少回补一点，想减伤就多回补一点，想回血就直接写。
    全程不需要注入 DLL。

    关键：每次改完血量都要把「期望值」写回 state.hp_prev，
    否则下一帧会把自己造成的额外伤害当成新的一次命中，无限递归。
"""

import ctypes
import ctypes.wintypes as wt
import math
import random
import struct
import time

# ============================================================ Win32
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.OpenProcess.restype = wt.HANDLE
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.ReadProcessMemory.restype = wt.BOOL
k32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.WriteProcessMemory.restype = wt.BOOL
k32.VirtualProtectEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                 wt.DWORD, ctypes.POINTER(wt.DWORD)]
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
k32.CreateToolhelp32Snapshot.restype = wt.HANDLE

PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_QUERY_INFORMATION = 0x0400
PAGE_EXECUTE_READWRITE = 0x40

EXE_NAME = "PlantsVsZombies.exe"

# ---- 基址 / Board 字段 ----
GAPP_PTR = 0x006A9EC0
OFF_BOARD = 0x768

B_ZOMBIE = 0x090
B_ZOMBIE_MAX = 0x094
B_PLANT = 0x0AC
B_PLANT_MAX = 0x0B0
B_SCENE = 0x554C
B_LEVEL = 0x5550
B_SUN = 0x5560
B_WAVE_TOTAL = 0x5564
B_WAVE = 0x5568

# ---- 对象内偏移 ----
P_ROW, P_TYPE, P_COL = 0x1C, 0x24, 0x28
P_HP, P_HP_MAX = 0x40, 0x44          # 血量 / 最大血量（实测豌豆射手 300/300）
P_DEAD = 0x141
Z_TYPE, Z_STATUS, Z_HP, Z_DEAD = 0x24, 0x28, 0xC8, 0xEC
# 僵尸的「防具」三件套（原生字段，游戏自己结算吸收）
Z_HELMET, Z_HELMET_MAX = 0xD0, 0xD4          # 头盔当前 / 上限（路障、铁桶…）
Z_SHIELD_TYPE = 0xD8                          # 护盾类型，0 = 没有盾
Z_SHIELD, Z_SHIELD_MAX = 0xDC, 0xE0           # 护盾当前 / 上限（铁门、梯子…）
O_X, O_Y, O_W, O_H = 0x08, 0x0C, 0x10, 0x14
# ---- 植物射击计时（0x45F8A9 处逆向所得）----
# +0x58 每帧 -1，<=0 时调用 0x45ef10 生成子弹，并把 +0x5C 减去一个 0~15 的随机抖动后写回。
# 所以 +0x5C 就是「射击间隔（帧）」，直接改小 = 真的多射。
P_SHOOT_COUNTER = 0x58
P_SHOOT_INTERVAL = 0x5C
# ---- 僵尸的浮点坐标 / 移速（v1.1 逆向所得，见 docs/hextech_values.md）----
# +0x08 mX 只是整数镜像：游戏每帧 fld [esi+0x2c] → float→int → mov [esi+8]
O_FX = 0x2C            # float 真实坐标 X
O_FY = 0x30            # float 真实坐标 Y
Z_SPEED_FIELD = 0x34   # float 每帧位移量（= 移速），由 UpdateZombieSpeed 维护
Z_ANIM_FIELD = 0x11C   # float 动画速率（默认 1.0，不是移速）

PLANT_STRIDE = 0x14C
ZOMBIE_STRIDE = 0x15C

MAX_PLANTS = 512
MAX_ZOMBIES = 1024

# ---- 种子银行 / 卡槽（v1.7 逆向所得，见 docs/hextech_values.md §盲盒卡）----
# 证据链：
#   0x40B1DA  mov ecx, [ebp+0x144]        ; SeedBank = Board->mSeedBank
#   0x40B1E0  mov [ecx+0x24], eax         ; mNumPackets
#   0x40B1F6  lea esi, [ebx+edx+0x28]     ; 第一张卡在 SeedBank+0x28
#   0x40B204  add ebx, 0x50               ; 步长 0x50
#   0x40B20A  cmp ebx, 0x320              ; 0x320/0x50 = 10 张
#   0x489AC0  SeedBank::RemoveSeed(idx)   ; 同样按 +0x28 + idx*0x50 定位
#   0x489ACD  lea eax, [eax+esi+0x5c]     ; = packet+0x34
#   0x489AD4  cmp [eax], -1               ; 空槽的 mPacketType 是 -1
#   0x489B57  mov [esi+0x34], edi         ; SetPacketType: mPacketType
#   0x489B5A  mov [esi+0x38], edx         ;                mImitaterType
#   0x488F7D  mov [edi+0x28], eax         ; 冷却 = gPlantDefs[type].mRefreshTime
B_SEEDBANK = 0x144          # Board + 0x144 = SeedBank*
SB_NUM = 0x24               # SeedBank + 0x24 = 本关卡槽数量
SB_FIRST = 0x28             # SeedBank + 0x28 = 第一张 SeedPacket
SEED_STRIDE = 0x50          # sizeof(SeedPacket)
SEED_MAX = 10               # 0x320 / 0x50
# SeedPacket 也是 GameObject：+0x00 = gLawnApp、+0x04 = Board。
# 这两项当指纹用 —— 比任何猜测的字段都可靠。
SEED_GAPP = 0x00
SEED_BOARD = 0x04
SEED_REFRESH = 0x28         # 冷却剩余帧数
SEED_TYPE = 0x34            # mPacketType（种什么就看这个）
SEED_IMITATER = 0x38        # mImitaterType（只有变身茄子卡用）
SEED_ACTIVE = 0x48          # byte，1 = 可以点
SEED_COOLING = 0x49         # byte，1 = 冷却中
SEED_USES = 0x4C            # int，用了几次（只增不减，用来检测「刚种下」）
# SeedType 合法范围是 -1 .. 51（不是 48）。49/50/51 是迷你游戏 / 禅境花园专用的
# 爆炸坚果 / 巨型坚果 / 幼苗，gPlantDefs 里有完整条目。52 之后是 0xFF 填充。
SEED_TYPE_MAX = 51
SEED_TYPE_PLAYABLE_MAX = 48   # 玩家能选的卡：0..48

# ---- 植物定义表 gPlantDefs（0x488F76  lea edx,[ecx+ecx*8] → *0x24）----
#   0x488F76  mov eax, [edx*4+0x69f2c4]   ; = gPlantDefs[type].mRefreshTime
GPLANTDEFS = 0x69F2C0       # 注意：不是 0x69F2C4，那只是「冷却」字段
PLANTDEF_STRIDE = 0x24
PLANTDEF_COST = 0x00
PLANTDEF_REFRESH = 0x04
PLANTDEF_RATE = 0x0C

# ---- 每个对象带几个海克斯 ----
# "random"：每个植物 / 僵尸各自随机 1~3 个（当前采用）
# "wave"  ：按波次 min(wave+1, 3)（Board+0x5568 的语义还没吃准，先不用）
COUNT_MODE = "random"
HEX_MIN, HEX_MAX = 1, 3
HEX_CAP = 3
# 究极档（目前只有「大肥鱼」）不占普通池的名额：先掷一次，掷中了才把它
# 并进候选池。8% × 池子里约 1/23 ≈ 每株 0.7%，够稀罕但不至于见不到。
PRISMATIC_CHANCE = 0.08


def hex_count_for_wave(wave):
    """第 1 波 2 个、第 2 波 3 个、之后封顶 3。"""
    return max(1, min(int(wave) + 1, HEX_CAP))


def roll_count(rng=None, wave=None):
    rng = rng or random
    if COUNT_MODE == "wave" and wave is not None:
        return hex_count_for_wave(wave)
    return rng.randint(HEX_MIN, HEX_MAX)


# ============================================================ 植物分类
# PVZ PlantType 枚举顺序（1.0.0.1051）
OUTPUT_PLANTS = {
    0, 5, 7, 8, 10, 18, 21, 26, 28, 29,
    32, 34, 39, 40, 42, 43, 44, 46, 47,
}
TANK_PLANTS = {3, 23, 30, 36}
# 承伤类：坚果墙 / 高坚果 / 南瓜头 / 大蒜
# 花盆（33）曾经也在这里，但 25 阳光换「泰坦巨人」300→900 血太便宜，踢出去了。
# 代价是花盆的池子只剩「会心防御」一条 —— 反正它就是个托盘。
SLOW_PLANTS = {5, 44}
# 生产类植物：向日葵 / 阳光菇 / 金盏花 / 双子向日葵 —— 专属光环词条
# （它们既不在输出、也不在承伤/减速里，不归到这里的话池子只剩「会心防御」一条）
SUN_PLANTS = {1, 9, 38, 41}

PLANT_NAMES = {
    0: "豌豆射手", 1: "向日葵", 2: "樱桃炸弹", 3: "坚果墙", 4: "土豆雷",
    5: "寒冰射手", 6: "大嘴花", 7: "双发射手", 8: "小喷菇", 9: "阳光菇",
    10: "大喷菇", 11: "墓碑吞噬者", 12: "魅惑菇", 13: "胆小菇", 14: "寒冰菇",
    15: "毁灭菇", 16: "睡莲", 17: "窝瓜", 18: "三线射手", 19: "缠绕水草",
    20: "火爆辣椒", 21: "地刺", 22: "火炬树桩", 23: "高坚果", 24: "水兵菇",
    25: "路灯花", 26: "仙人掌", 27: "三叶草", 28: "裂荚射手", 29: "杨桃",
    30: "南瓜头", 31: "磁力菇", 32: "卷心菜投手", 33: "花盆", 34: "玉米投手",
    35: "咖啡豆", 36: "大蒜", 37: "叶子保护伞", 38: "金盏花", 39: "西瓜投手",
    40: "机枪射手", 41: "双子向日葵", 42: "忧郁菇", 43: "香蒲", 44: "冰西瓜",
    45: "吸金磁", 46: "地刺王", 47: "玉米加农炮", 48: "变身茄子",
}

ZOMBIE_NAMES = {
    0: "普通僵尸", 1: "旗帜僵尸", 2: "路障僵尸", 3: "撑杆僵尸", 4: "铁桶僵尸",
    5: "报纸僵尸", 6: "铁门僵尸", 7: "橄榄球僵尸", 8: "舞王僵尸", 9: "伴舞僵尸",
    10: "鸭子救生圈僵尸", 11: "潜水僵尸", 12: "冰车僵尸", 13: "雪橇车僵尸",
    14: "海豚骑士僵尸", 15: "跳跳僵尸", 16: "小丑僵尸", 17: "气球僵尸",
    18: "矿工僵尸", 19: "蹦极僵尸", 20: "梯子僵尸", 21: "投石车僵尸",
    22: "巨人僵尸", 23: "小鬼僵尸", 24: "僵王博士", 25: "豌豆僵尸",
    26: "坚果僵尸", 27: "辣椒僵尸", 28: "机枪僵尸", 29: "高坚果僵尸",
    30: "雪人僵尸", 31: "窝瓜僵尸", 32: "海豚骑士僵尸", 33: "小鬼大炮",
}

# 各植物的阳光成本（原版数值）。【泰坦巨人】按这个表额外再扣一份。
PLANT_COST = {
    0: 100, 1: 50, 2: 150, 3: 50, 4: 25, 5: 175, 6: 150, 7: 200, 8: 0,
    9: 25, 10: 75, 11: 25, 12: 75, 13: 25, 14: 75, 15: 125, 16: 25,
    17: 50, 18: 325, 19: 25, 20: 125, 21: 100, 22: 175, 23: 125, 24: 0,
    25: 25, 26: 125, 27: 100, 28: 125, 29: 125, 30: 125, 31: 100,
    32: 100, 33: 25, 34: 100, 35: 75, 36: 50, 37: 100, 38: 50, 39: 300,
    40: 250, 41: 150, 42: 150, 43: 225, 44: 200, 45: 50, 46: 125,
    47: 500, 48: 0,
}

# ============================================================ 词条定义
#
# scope:  plant / zombie
# apply:  output（输出植物）/ tank（承伤植物）/ slow（带减速）/ all
# impl:   True = 本期已实现；False = 等攻速/移速字段（阶段 2）
#
# tier 预留：silver / gold / prismatic，本期只有 silver

SILVER = "silver"
GOLD = "gold"
PRISMATIC = "prismatic"

TIER_COLOR = {
    SILVER: "#C8D2DC",
    GOLD: "#F0B429",
    PRISMATIC: "#B57BFF",
}
TIER_NAME = {SILVER: "银色", GOLD: "金色", PRISMATIC: "棱彩"}

HEX_DEFS = [
    # ---------------- 植物 ----------------
    dict(id="p_swift", name="灵巧", short="灵", tier=SILVER, scope="plant",
         apply="output", impl=True, need="缩短射击间隔 +0x5C（真实多射）",
         desc="攻速 +25%。"),
    dict(id="p_chill", name="冰寒", short="冰", tier=SILVER, scope="plant",
         apply="slow", impl=True, need="减速补位移",
         desc="命中后的减速效果额外强化 30%，持续 3 秒。"),
    dict(id="p_mountain", name="山脉龙魂", short="山", tier=SILVER, scope="plant",
         apply="tank", impl=True,
         desc="获得基于血量百分比的护盾，3 秒内未受到伤害即开始恢复。"),
    dict(id="p_storm", name="电龙魂", short="电", tier=SILVER, scope="plant",
         apply="output", impl=True,
         desc="攻击命中僵尸后，对周围僵尸造成无视护甲的伤害并减移速，每隔一段时间触发一次。"),
    dict(id="p_power", name="大力", short="力", tier=SILVER, scope="plant",
         apply="output", impl=True,
         desc="伤害提升。"),
    dict(id="p_zealot", name="狂热者", short="狂", tier=SILVER, scope="plant",
         apply="output", impl=True,
         desc="攻速加快，并有 25% 概率造成双倍伤害。"),
    dict(id="p_sacrifice", name="升级献祭", short="献", tier=SILVER, scope="plant",
         apply="tank", impl=True,
         desc="每秒对前方六格的僵尸造成基于生命值的伤害，并获得一定阳光。"),
    dict(id="p_critdef", name="会心防御", short="御", tier=SILVER, scope="plant",
         apply="all", impl=True,
         desc="每次承受伤害有 25% 概率完全无视该伤害。"),
    dict(id="p_farsight", name="远射天资", short="远", tier=SILVER, scope="plant",
         apply="output", impl=True,
         desc="僵尸在前两格草坪时受到该植物攻击，额外造成伤害。"),
    dict(id="p_rageblade", name="羊刀", short="羊", tier=SILVER, scope="plant",
         apply="output", impl=True, need="缩短射击间隔 +0x5C（真实多射）",
         desc="攻击每命中一次增加 1% 攻速，最多 +100%。"),
    dict(id="p_collector", name="收集者", short="收", tier=SILVER, scope="plant",
         apply="output", impl=True,
         desc="杀死僵尸后获得 50 阳光。"),

    # ---------------- 植物 v1.6 新增 ----------------
    dict(id="p_bigpea", name="大腕", short="腕", tier=SILVER, scope="plant",
         apply="output", impl=True, need="攻速 ×0.125、伤害 ×6、命中整格击退",
         desc="攻速降低 87.5%，但射出的豌豆伤害 6 倍，并把命中的那一格的所有僵尸击退一格。"),
    dict(id="p_bloodsword", name="花晓之剑", short="剑", tier=SILVER, scope="plant",
         apply="output", impl=True,
         desc="对僵尸造成伤害时，回复自身已损失生命值。"),
    dict(id="p_titanresolve", name="泰坦的坚决", short="坚", tier=SILVER, scope="plant",
         apply="tank", impl=True,
         desc="每次受到伤害，永久增加 5% 免伤（最多 75%）。"),
    dict(id="p_thornmail", name="荆棘之甲", short="荆", tier=SILVER, scope="plant",
         apply="tank", impl=True,
         desc="反弹 50% 所受伤害给正在啃食它的僵尸。"),
    dict(id="p_titan", name="泰坦巨人", short="泰", tier=SILVER, scope="plant",
         apply="tank", impl=True, need="种植额外扣阳光 + 血量上限 ×3",
         desc="种植时额外消耗 100% 的阳光，但血量上限变为 300%。"),
    dict(id="p_giantkiller", name="巨人杀手", short="巨", tier=SILVER, scope="plant",
         apply="output", impl=True,
         desc="基于僵尸最大生命值造成额外伤害：每 10 点血多 1%，最多 +200%。"),

    # ---------------- 向日葵专属（v1.0） ----------------
    dict(id="p_devotion", name="全心为你", short="心", tier=SILVER, scope="plant",
         apply="sun", impl=True,
         desc="周围植物即将死亡时，向日葵替它去死，并让该植物回复 100 血。"),
    dict(id="p_inspire", name="鼓舞", short="鼓", tier=SILVER, scope="plant",
         apply="sun", impl=True,
         desc="周围植物伤害 +20%。"),
    dict(id="p_windbless", name="风语者祝福", short="风", tier=SILVER, scope="plant",
         apply="sun", impl=True,
         desc="周围植物获得 25% 免伤。"),
    dict(id="p_guardian", name="守护", short="守", tier=SILVER, scope="plant",
         apply="sun", impl=True,
         desc="每 5 秒为随机一株植物回复 100 血。"),

    # ---------------- 究极（移植自融合版，见 docs/hextech_values.md §大肥鱼）----
    # 融合版的「DeepSeek大肥鱼」是究极级二创植物：80000 血、无冷却、全场索敌、
    # 5×5 核爆 8000、8% 概率全场每格 5 万。
    # 它搬不过来的只有外观动画（融合版自己移植别人植物时也卡在同一处，
    # 最后只能烘焙成一张静态贴图）；玩法全部能用「血量通道 + 范围伤害」还原。
    dict(id="p_deepfish", name="大肥鱼", short="鱼", tier=PRISMATIC,
         scope="plant", impl=True, apply="all",
         need="血量 8 万、全场索敌、5×5 核爆",
         desc="血量 8 万。每 0.5 秒锁定离房子最近的僵尸，在其脚下引爆 5×5 格核爆"
              "（8000 伤害），不分行、不分左右；每次出手另有 8% 概率引发全场核爆"
              "（每只 5 万）。"),

    # ---------------- 僵尸 ----------------
    dict(id="z_escape", name="逃跑计划", short="逃", tier=SILVER, scope="zombie",
         impl=True, need="移速补位移",
         desc="血量降到 50% 时触发：获得 100% 生命值的护盾，5 秒内衰减；"
              "同时获得 200% 移速（即 ×3），与护盾同步衰减。"),
    dict(id="z_heavy", name="重量级打手", short="重", tier=SILVER, scope="zombie",
         impl=True,
         desc="基于自身最大生命值造成一定比例的额外伤害。"),
    dict(id="z_bloodthirst", name="渴血", short="渴", tier=SILVER, scope="zombie",
         impl=True,
         desc="回复造成伤害一定比例的血量。"),
    dict(id="z_homeguard", name="家园卫士", short="家", tier=SILVER, scope="zombie",
         impl=True, need="移速补位移",
         desc="在受到伤害之前移速翻倍（被打过一次就失效）。"),
    dict(id="z_deathdance", name="死之舞", short="舞", tier=SILVER, scope="zombie",
         impl=True,
         desc="受到的 40% 伤害改为 3 秒内持续流血；期间杀死植物则免除这部分伤害并回血。"),
    dict(id="z_swift", name="灵巧", short="灵", tier=SILVER, scope="zombie",
         impl=True, need="啃食间隔未定位，用等量增伤",
         desc="啃食速度 +25%。"),
    dict(id="z_legday", name="练腿日", short="腿", tier=SILVER, scope="zombie",
         impl=True, need="移速补位移",
         desc="移速 +50%，并免疫减速效果（冰冻按未减速的速度算）。"),
    dict(id="z_lone", name="孤勇者", short="孤", tier=SILVER, scope="zombie",
         impl=True, need="移速补位移 + 攻速用等量增伤",
         desc="周围 9 宫格没有其他僵尸时，获得 50% 免伤、100% 移速与 50% 攻速。"),
    dict(id="z_scope", name="万用瞄准镜", short="镜", tier=SILVER, scope="zombie",
         impl=True,
         desc="可以隔一格吃到植物。"),
    dict(id="z_mountain", name="山脉龙魂", short="山", tier=SILVER, scope="zombie",
         impl=True,
         desc="获得基于血量百分比的护盾，3 秒内未受到伤害即直接回满。"),
    dict(id="z_zealot", name="狂热者", short="狂", tier=SILVER, scope="zombie",
         impl=True, need="啃食间隔未定位，用等量增伤",
         desc="攻速 +15%，并有 25% 概率造成双倍伤害。"),

    # ---------------- v1.0 新增 ----------------
    dict(id="z_armorcrash", name="装甲冲击", short="甲", tier=SILVER, scope="zombie",
         impl=True,
         desc="第一次进攻时，按自身护甲与护盾值造成一次额外范围伤害。"),
    dict(id="z_unyielding", name="屹立不倒", short="屹", tier=SILVER, scope="zombie",
         impl=True, need="啃食间隔未定位，用等量增伤",
         desc="按已损失生命值获得最多 +200% 攻速与 70% 吸血。"),
    dict(id="z_scope2", name="更万用瞄准镜", short="镜+", tier=SILVER, scope="zombie",
         impl=True,
         desc="可以隔两格吃到植物。"),
    dict(id="z_astral", name="星界躯体", short="星", tier=SILVER, scope="zombie",
         impl=True,
         desc="额外 +1800 生命值，但造成的伤害 -30%。"),
    dict(id="z_bang", name="邦！", short="邦", tier=SILVER, scope="zombie",
         impl=True,
         desc="每次攻击都对周围植物造成范围伤害。"),
    dict(id="z_flash", name="闪现向前", short="闪", tier=SILVER, scope="zombie",
         impl=True,
         desc="越过当前植物，每只僵尸可触发 2 次。"),
    dict(id="z_porcupine", name="豪猪", short="豪", tier=SILVER, scope="zombie",
         impl=True,
         desc="反弹 20% 所受伤害给攻击它的植物。"),

    # ---------------- 僵尸 v1.6 新增 ----------------
    dict(id="z_goliath", name="歌莉娅巨人", short="歌", tier=SILVER, scope="zombie",
         impl=True, need="血量 ×2 + 伤害 +50%",
         desc="血量增加 100%，造成的伤害增加 50%。"),
    dict(id="z_madman", name="科学狂人", short="科", tier=SILVER, scope="zombie",
         impl=True, need="碰撞盒缩小 + 移速补位移",
         desc="体型缩小，移速增加 150%。"),
    dict(id="z_clowncollege", name="小丑学院", short="丑", tier=SILVER, scope="zombie",
         impl=True, need="死亡时原位范围爆炸",
         desc="死亡后在自身所在位置爆炸，把周围一格内的植物几乎全部清空。"),
    dict(id="z_smallkiller", name="小小杀手", short="小", tier=SILVER, scope="zombie",
         impl=True, need="碰撞盒缩小 + 移速补位移 + 伤害 +50%",
         desc="体型缩小，移速增加 100%，造成的伤害增加 50%。"),
    dict(id="z_baron", name="男爵之手", short="爵", tier=SILVER, scope="zombie",
         impl=True, need="全场僵尸光环",
         desc="在场时强化所有僵尸：移速 +50%，伤害 +50%。"),
    dict(id="z_titanresolve", name="泰坦的坚决", short="坚", tier=SILVER, scope="zombie",
         impl=True,
         desc="每次受到伤害，永久增加 5% 免伤（最多 75%）。"),
    dict(id="z_waterdragon", name="水龙魂", short="水", tier=SILVER, scope="zombie",
         impl=True,
         desc="受伤后或在啃食植物时，每秒回复 20 点生命值。"),
    dict(id="z_unstoppable", name="终极不可阻挡", short="阻", tier=SILVER,
         scope="zombie", impl=True, need="受伤后 10 秒免疫控制，仅一次",
         desc="首次受伤后 10 秒内免疫一切控制：不被减速、不被冰冻拖住、不被击退。每只僵尸只触发一次。"),
]

# 互斥组：同一只僵尸身上不会同时出现组内两条
# 1) 山脉龙魂 与 逃跑计划 抢同一个原生护盾槽（Zombie+0xDC），必须互斥。
# 2) 四条移速词条互斥（v1.1）：移速是**加法叠乘**的，两条一起就是 ×5 以上，
#    三、四条直接飞起来（×3 ×2 ×2 ×1.5 ≈ 18 倍），必须限制成只能吃一条。
EXCLUSIVE_GROUPS = [
    {"z_mountain", "z_escape"},
    # 所有「自身移速加成」互斥：全叠加会让僵尸直接飞过去
    {"z_escape", "z_lone", "z_homeguard", "z_legday",
     "z_madman", "z_smallkiller"},
    {"z_scope", "z_scope2"},          # 隔 1 格 / 隔 2 格，后者完全覆盖前者
    # 两条「体型缩小」都是缩体型 + 加速，叠加没有意义
    {"z_madman", "z_smallkiller"},
]

HEX_BY_ID = {d["id"]: d for d in HEX_DEFS}


def pool_for(kind, plant_type=None, tier=None):
    """按对象类型 + 档位筛出可抽取的词条池。"""
    out = []
    for d in HEX_DEFS:
        if d["scope"] != kind:
            continue
        if tier and d["tier"] != tier:
            continue
        if kind == "plant" and plant_type is not None:
            a = d.get("apply", "all")
            if a == "output" and plant_type not in OUTPUT_PLANTS:
                continue
            if a == "tank" and plant_type not in TANK_PLANTS:
                continue
            if a == "slow" and plant_type not in SLOW_PLANTS:
                continue
            if a == "sun" and plant_type not in SUN_PLANTS:
                continue
        out.append(d)
    return out


def roll_hexes(kind, count, plant_type=None, rng=None):
    """无放回抽 count 个词条（不可重复，且互斥组内最多一条）。"""
    rng = rng or random
    pool = pool_for(kind, plant_type, tier=SILVER)
    # 究极档低概率单独放进来（不占名额，掷中了才并池）
    if rng.random() < PRISMATIC_CHANCE:
        pool = pool + pool_for(kind, plant_type, tier=PRISMATIC)
    if not pool:
        return []
    count = min(count, len(pool))
    ids = [d["id"] for d in pool]
    rng.shuffle(ids)
    picked = []
    for hid in ids:
        if len(picked) >= count:
            break
        blocked = False
        for grp in EXCLUSIVE_GROUPS:
            if hid in grp and any(x in grp for x in picked):
                blocked = True
                break
        if not blocked:
            picked.append(hid)
    return picked


# ============================================================ 进程
class PE32(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_char * 260)]


def find_pid(name=EXE_NAME):
    TH32CS_SNAPPROCESS = 0x02
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == wt.HANDLE(-1).value:
        return None
    e = PE32()
    e.dwSize = ctypes.sizeof(PE32)
    ok = k32.Process32First(snap, ctypes.byref(e))
    while ok:
        if e.szExeFile.decode("mbcs", "ignore").lower() == name.lower():
            k32.CloseHandle(snap)
            return e.th32ProcessID
        ok = k32.Process32Next(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return None


class Game:
    def __init__(self, pid=None):
        if pid is None:
            pid = find_pid()
        if not pid:
            raise SystemExit("找不到 %s，请先启动游戏并进入关卡。" % EXE_NAME)
        self.pid = pid
        self.h = k32.OpenProcess(PROCESS_VM_OPERATION | PROCESS_VM_READ
                                 | PROCESS_VM_WRITE | PROCESS_QUERY_INFORMATION,
                                 False, pid)
        if not self.h:
            raise SystemExit("OpenProcess 失败 (err=%d)，试试管理员权限。"
                             % ctypes.get_last_error())

    def close(self):
        if self.h:
            k32.CloseHandle(self.h)
            self.h = None

    # ---------- 读写 ----------
    def read(self, addr, n):
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t(0)
        ok = k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, n,
                                   ctypes.byref(got))
        if not ok or got.value != n:
            return None
        return buf.raw

    def u32(self, addr):
        b = self.read(addr, 4)
        return struct.unpack("<I", b)[0] if b else None

    def i32(self, addr):
        b = self.read(addr, 4)
        return struct.unpack("<i", b)[0] if b else None

    def u8(self, addr):
        b = self.read(addr, 1)
        return b[0] if b else None

    def f32(self, addr):
        """读 float。僵尸坐标(+0x2C) / 移速(+0x34) 都是 float 字段。"""
        b = self.read(addr, 4)
        return struct.unpack("<f", b)[0] if b else None

    def _write(self, addr, data):
        old = wt.DWORD(0)
        n = len(data)
        if not k32.VirtualProtectEx(self.h, ctypes.c_void_p(addr), n,
                                    PAGE_EXECUTE_READWRITE, ctypes.byref(old)):
            return False
        got = ctypes.c_size_t(0)
        ok = k32.WriteProcessMemory(self.h, ctypes.c_void_p(addr), data, n,
                                    ctypes.byref(got))
        k32.VirtualProtectEx(self.h, ctypes.c_void_p(addr), n, old.value,
                             ctypes.byref(old))
        return bool(ok) and got.value == n

    def write_i32(self, addr, value):
        return self._write(addr, struct.pack("<i", int(value)))

    def write_u32(self, addr, value):
        return self._write(addr, struct.pack("<I", value & 0xFFFFFFFF))

    def write_u8(self, addr, value):
        return self._write(addr, bytes([value & 0xFF]))

    def write_f32(self, addr, value):
        return self._write(addr, struct.pack("<f", float(value)))

    # ---------- 游戏对象 ----------
    @property
    def gapp(self):
        return self.u32(GAPP_PTR)

    @property
    def board(self):
        g = self.gapp
        return self.u32(g + OFF_BOARD) if g else None

    def sun(self):
        b = self.board
        return self.i32(b + B_SUN) if b else None

    def add_sun(self, delta):
        b = self.board
        if not b:
            return
        cur = self.i32(b + B_SUN)
        if cur is None:
            return
        self.write_i32(b + B_SUN, max(0, min(cur + int(delta), 99999)))

    def wave(self):
        b = self.board
        return self.i32(b + B_WAVE) if b else None

    def wave_total(self):
        b = self.board
        return self.i32(b + B_WAVE_TOTAL) if b else None

    def level(self):
        b = self.board
        return self.i32(b + B_LEVEL) if b else None

    # ---------- 数组扫描 ----------
    def scan_plants(self):
        """返回 [dict]，每个元素是一株存活植物。"""
        b = self.board
        if not b:
            return []
        base = self.u32(b + B_PLANT)
        cap = self.i32(b + B_PLANT_MAX) or 0
        if not base or cap <= 0:
            return []
        cap = min(cap, MAX_PLANTS)
        raw = self.read(base, cap * PLANT_STRIDE)
        if not raw:
            return []
        gapp = self.gapp
        out = []
        for i in range(cap):
            off = i * PLANT_STRIDE
            head = struct.unpack_from("<II", raw, off)
            if head[0] != gapp or head[1] != b:
                continue
            if raw[off + P_DEAD]:
                continue
            hp = struct.unpack_from("<i", raw, off + P_HP)[0]
            if hp <= 0:
                continue
            hp_max = struct.unpack_from("<i", raw, off + P_HP_MAX)[0]
            out.append(dict(
                addr=base + off, kind="plant", idx=i,
                type=struct.unpack_from("<i", raw, off + P_TYPE)[0],
                row=struct.unpack_from("<i", raw, off + P_ROW)[0],
                col=struct.unpack_from("<i", raw, off + P_COL)[0],
                hp=hp,
                hp_max=hp_max if hp_max > 0 else hp,
                x=struct.unpack_from("<i", raw, off + O_X)[0],
                y=struct.unpack_from("<i", raw, off + O_Y)[0],
                w=struct.unpack_from("<i", raw, off + O_W)[0],
                h=struct.unpack_from("<i", raw, off + O_H)[0],
            ))
        return out

    def scan_zombies(self):
        b = self.board
        if not b:
            return []
        base = self.u32(b + B_ZOMBIE)
        cap = self.i32(b + B_ZOMBIE_MAX) or 0
        if not base or cap <= 0:
            return []
        cap = min(cap, MAX_ZOMBIES)
        raw = self.read(base, cap * ZOMBIE_STRIDE)
        if not raw:
            return []
        gapp = self.gapp
        out = []
        for i in range(cap):
            off = i * ZOMBIE_STRIDE
            head = struct.unpack_from("<II", raw, off)
            if head[0] != gapp or head[1] != b:
                continue
            if raw[off + Z_DEAD]:
                continue
            hp = struct.unpack_from("<i", raw, off + Z_HP)[0]
            if hp <= 0:
                continue
            out.append(dict(
                addr=base + off, kind="zombie", idx=i,
                type=struct.unpack_from("<i", raw, off + Z_TYPE)[0],
                status=struct.unpack_from("<i", raw, off + Z_STATUS)[0],
                hp=hp,
                helmet=struct.unpack_from("<i", raw, off + Z_HELMET)[0],
                helmet_max=struct.unpack_from("<i", raw, off + Z_HELMET_MAX)[0],
                shield_type=struct.unpack_from("<i", raw, off + Z_SHIELD_TYPE)[0],
                shield=struct.unpack_from("<i", raw, off + Z_SHIELD)[0],
                shield_max=struct.unpack_from("<i", raw, off + Z_SHIELD_MAX)[0],
                x=struct.unpack_from("<i", raw, off + O_X)[0],
                y=struct.unpack_from("<i", raw, off + O_Y)[0],
                w=struct.unpack_from("<i", raw, off + O_W)[0],
                h=struct.unpack_from("<i", raw, off + O_H)[0],
            ))
        return out

    # ---------- 植物搬格子（同格叠种 = 融合）----------
    # 为什么能这么干：Board::GetPlantAt(0x40D1A0) 是「线性扫植物数组，
    # 比对 Plant+0x1C(row) / +0x28(col)」，没有网格缓存数组。
    # 而南瓜头 / 睡莲 / 花盆 / 咖啡豆本来就和宿主同格 —— 引擎原生支持一格里有多株，
    # 只是靠 Plant+0x134 / +0x142 之类的标志位决定谁是「格子的主体」。
    # 所以把一株的 row/col/mX/mY 改到另一株的格子上，两株就叠住了。
    def move_plant(self, addr, row=None, col=None, x=None, y=None):
        ok = True
        if row is not None:
            ok = self.write_i32(addr + P_ROW, row) and ok
        if col is not None:
            ok = self.write_i32(addr + P_COL, col) and ok
        if x is not None:
            ok = self.write_i32(addr + O_X, x) and ok
        if y is not None:
            ok = self.write_i32(addr + O_Y, y) and ok
        return bool(ok)

    def fuse_plant(self, src, dst):
        """把 src 搬到 dst 的格子上。

        坐标直接抄 dst 的现值，不自己算 —— 泳池 / 屋顶 / 月夜这些场景的
        格子基准不一样，抄现值永远对得上。
        """
        if src["addr"] == dst["addr"]:
            return False
        return self.move_plant(src["addr"], row=dst["row"], col=dst["col"],
                               x=dst["x"], y=dst["y"])

    # ---------- 种子银行 ----------
    def seedbank(self):
        """返回 SeedBank 基址，读不到就是 None。"""
        b = self.board
        if not b:
            return None
        return self.u32(b + B_SEEDBANK)

    def scan_seedbank(self):
        """返回 [dict]，每个元素是一张卡槽。

        字段自检（任何一项对不上就整段作废，宁可不干活也别乱写内存）：
            mNumPackets 必须落在 1..10
            每张卡的 +0x00/+0x04 必须等于 gLawnApp / Board（GameObject 指纹）
            mPacketType 必须是 -1 或 0..51（49/50/51 是迷你游戏植物）
        """
        sb = self.seedbank()
        b = self.board
        if not sb or not b:
            return []
        n = self.i32(sb + SB_NUM)
        if n is None or not (1 <= n <= SEED_MAX):
            return []
        raw = self.read(sb + SB_FIRST, SEED_MAX * SEED_STRIDE)
        if not raw:
            return []
        gapp = self.gapp
        out = []
        for i in range(n):
            off = i * SEED_STRIDE
            if struct.unpack_from("<II", raw, off) != (gapp, b):
                return []          # 指纹不对，结构猜错了
            ptype, imit = struct.unpack_from("<ii", raw, off + SEED_TYPE)
            if not (-1 <= ptype <= SEED_TYPE_MAX):
                return []
            out.append(dict(
                addr=sb + SB_FIRST + off, kind="seed", slot=i,
                type=ptype,
                imitater=imit,
                refresh=struct.unpack_from("<i", raw,
                                           off + SEED_REFRESH)[0],
                active=raw[off + SEED_ACTIVE],
                cooling=raw[off + SEED_COOLING],
                uses=struct.unpack_from("<i", raw, off + SEED_USES)[0],
                x=struct.unpack_from("<i", raw, off + O_X)[0],
                y=struct.unpack_from("<i", raw, off + O_Y)[0],
            ))
        return out
        return out


# ============================================================ 每对象状态
class ObjState:
    __slots__ = ("hexes", "hp_prev", "hp_max", "shield", "shield_max",
                 "last_hit", "dot_rate", "dot_until", "fired",
                 "last_aoe", "hits", "born", "last_src",
                 "escape_t", "flash_left", "obs_rate", "prev_x",
                 "pending", "astral", "scope_acc", "rage_hits",
                 "chill_until", "chill_ratio", "chill_prev_x",
                 "shoot_base",
                 # v1.6
                 "resolve_stacks", "scale_done", "goliath_done",
                 "titan_hp_done", "titan_sun_done", "base_w", "base_h",
                 # v1.6.2
                 "last_bite", "regen_acc",
                 # v1.6.6
                 "cc_until",
                 # v1.7.1
                 "fish_last")

    def __init__(self, hexes, hp):
        self.hexes = hexes
        self.hp_prev = hp
        self.hp_max = max(1, hp)
        self.shield = 0
        self.shield_max = 0
        self.last_hit = 0.0
        self.dot_rate = 0.0
        self.dot_until = 0.0
        self.fired = set()          # 一次性效果（逃跑计划 / 击杀奖励）已触发标记
        self.last_aoe = 0.0
        self.hits = 0
        self.born = time.time()
        self.last_src = None        # 最近一次攻击者的 addr（用于击杀归属）
        self.escape_t = 0.0         # 逃跑计划触发时刻
        self.flash_left = None      # 闪现剩余次数（None = 还没初始化）
        self.obs_rate = 0.0         # 观测到的僵尸自身速度（px/秒，负 = 朝左）
        self.prev_x = None          # 上一帧的 float 坐标（用于算自身速度）
        self.pending = 0.0          # 待补位移的累积（不足最小粒度时攒着）
        self.astral = False         # 星界躯体是否已加血
        self.scope_acc = 0.0        # 万用瞄准镜的伤害累加器
        self.rage_hits = 0          # 羊刀：已命中次数（每层 +1% 攻速）
        self.chill_until = 0.0      # 冰寒：额外减速到期时间
        self.chill_ratio = 0.0      # 冰寒：额外减速比例
        self.chill_prev_x = None    # 冰寒：上一帧补减速后的坐标
        self.shoot_base = None      # 植物原生射击间隔（+0x5C 未被改小的值）
        # ---- v1.6 ----
        self.resolve_stacks = 0     # 泰坦的坚决：已叠的免伤层数（每层 5%）
        self.scale_done = False     # 体型缩小是否已应用
        self.goliath_done = False   # 歌莉娅巨人血量是否已翻倍
        self.titan_hp_done = False  # 泰坦巨人血量上限是否已 ×3
        self.titan_sun_done = False # 泰坦巨人额外阳光是否已扣
        self.base_w = 0             # 原始宽（体型缩放前）
        self.base_h = 0             # 原始高
        # ---- v1.6.2 ----
        self.last_bite = 0.0        # 水龙魂：最近一次啃到植物的时刻
        self.regen_acc = 0.0        # 水龙魂：不足 1 点的回血攒着
        # ---- v1.6.6 ----
        self.cc_until = 0.0         # 终极不可阻挡：免疫控制的到期时刻
        self.fish_last = 0.0        # 大肥鱼：上次出手的时刻

    def has(self, hid):
        return hid in self.hexes


# ============================================================ 引擎
class Engine:
    """海克斯结算引擎。

    用法：
        eng = Engine(game)
        eng.tick()          # 每 1/30 秒调一次
        eng.objects         # 当前所有对象（含 hexes）供 UI 读取
    """

    # ---- 可调参数（想平衡就改这里）----
    AURA_RANGE_X = 85           # 光环 / 替死判定半径 X（像素）
    AURA_RANGE_Y = 100          # 光环 / 替死判定半径 Y（像素）
    Z_BASE_SPEED_PX = 23.0      # 普通僵尸真实移速（像素/游戏秒），读不到 +0x34 时兜底
    Z_SPEED_HZ = 100.0          # 游戏每秒 Update 次数（实测 Zombie+0x60 每秒 +100）
    Z_MOVE_DUTY = 0.45          # 真正推进位置的 Update 占比（实测 0.40~0.50 波动）
    Z_SPEED_MIN_STEP = 0.05     # 补位移的最小粒度（像素），太小就攒着
    Z_SPEED_SMOOTH = 0.35       # 观测自身速度的低通系数（越大反应越快、越抖）
    Z_SPEED_MAX = 10.0          # +0x34 合理上限，超界视为脏数据

    P_POWER = 0.35              # 大力：伤害 +35%
    P_SWIFT_SPEED = 0.25        # 灵巧：射击间隔缩短到 1/1.25（真的多射）
    P_RAGEBLADE_PER_HIT = 0.01  # 羊刀：每次命中 +1% 攻速（缩短射击间隔）
    P_RAGEBLADE_CAP = 1.00      # 羊刀：攻速加成上限 +100%
    P_SHOOT_INTERVAL_MIN = 1    # 射击间隔下限（帧），防止写出 0 导致每帧开火
    # 原版射击间隔的合理区间（帧）。原版会射击的植物只有两档：
    # 150（豌豆 / 蘑菇 / 仙人掌 / 杨桃 / 香蒲 / 机枪）和 300（投手类）。
    # 落在 [140, 400] 之外的读数，一定是 mod 自己写进去的，见 _native_shoot_base。
    SHOOT_NATIVE_MIN = 140
    SHOOT_NATIVE_MAX = 400
    P_CHILL_EXTRA = 0.30        # 冰寒：在原生减速之上再压 30% 移速
    P_CHILL_TIME = 3.0          # 冰寒：强化减速持续 3 秒
    P_ZEALOT_DOUBLE = 0.25      # 狂热者：双倍伤害概率
    P_ZEALOT_SPEED = 0.15       # 狂热者：射击间隔缩短到 1/1.15（真的多射）
    P_FARSIGHT = 0.5            # 远射天资：前两格额外 +50%
    P_FARSIGHT_RANGE = 200      # 远射天资：判定线（草坪左起 x<200 = 前两格）
    P_CRITDEF = 0.25            # 会心防御：25% 完全免伤
    P_STORM_CD = 5.0            # 电龙魂冷却（秒）
    P_STORM_RADIUS = 90         # 电龙魂波及半径（像素）
    P_STORM_DMG = 0.5           # 电龙魂：按本次命中伤害的比例
    P_SACRIFICE_CD = 1.0        # 升级献祭间隔
    P_SACRIFICE_RATIO = 0.02    # 升级献祭：自身最大血量 2%
    P_SACRIFICE_SUN = 5         # 升级献祭：每次给多少阳光
    P_SACRIFICE_RANGE = 6       # 升级献祭：前方几格
    P_COLLECTOR_SUN = 50        # 收集者：击杀给多少阳光

    P_MOUNTAIN_RATIO = 0.30     # 山脉龙魂：护盾 = 最大血量 30%
    P_MOUNTAIN_REGEN = 3.0      # 山脉龙魂：脱战几秒后开始回盾（v0.9: 6 → 3）
    P_MOUNTAIN_RATE = 0.15      # 山脉龙魂：每秒回复护盾上限的比例
    P_MOUNTAIN_FULL = True      # 山脉龙魂：True = 到点直接回满（v1.0 改）

    # ---- 向日葵专属（光环 / 支援）----
    P_DEVOTION_HEAL = 100       # 全心为你：被救植物回复到 100 血
    P_INSPIRE_DMG = 0.20        # 鼓舞：周围植物伤害 +20%
    P_WIND_DR = 0.25            # 风语者祝福：周围植物免伤 25%
    P_GUARDIAN_CD = 5.0         # 守护：每 5 秒触发
    P_GUARDIAN_HEAL = 100       # 守护：给随机植物 +100 血

    Z_ESCAPE_RATIO = 0.50       # 逃跑计划：触发血量比例（v0.9: 0.30 → 0.50）
    Z_ESCAPE_SHIELD = 1.00      # 逃跑计划：护盾 = 最大血量 100%（v1.0: 50% → 100%）
    Z_ESCAPE_DECAY = 5.0        # 逃跑计划：护盾衰减时间
    Z_ESCAPE_SPEED = 2.00       # 逃跑计划：移速 +200%（= ×3），与护盾同步衰减
    Z_HEAVY_RATIO = 0.05        # 重量级打手：额外伤害 = 自身最大血量 5%
    Z_BLOODTHIRST = 0.30        # 渴血：回复造成伤害的 30%
    Z_DEATHDANCE_RATIO = 0.40   # 死之舞：40% 伤害转为流血
    Z_DEATHDANCE_TIME = 3.0     # 死之舞：流血持续 3 秒
    Z_LONE_DR = 0.50            # 孤勇者：50% 免伤
    Z_LONE_RANGE_X = 85         # 孤勇者：判定半径
    Z_LONE_RANGE_Y = 100
    Z_LONE_SPEED = 1.00         # 孤勇者：+100% 移速（= ×2）
    Z_TITANRESOLVE_PER_HIT = 0.05   # 泰坦的坚决（僵尸版）：每次受伤 +5% 免伤
    Z_TITANRESOLVE_CAP = 0.75       # 泰坦的坚决（僵尸版）：免伤上限 75%
    Z_WATERDRAGON_REGEN = 20.0      # 水龙魂：每秒回复血量
    Z_WATERDRAGON_WINDOW = 3.0      # 水龙魂：受伤后 3 秒内都算「受伤状态」
    Z_WATERDRAGON_BITE = 1.0        # 水龙魂：1 秒内啃过植物就算「正在吃」
    Z_UNSTOPPABLE_TIME = 10.0       # 终极不可阻挡：首次受伤后免疫控制的时长
    # 免疫期间「正在啃植物」的判定窗口。啃食时僵尸本来就不动，
    # 这时候补位移会把它从植物嘴边推开，所以窗口内不推。
    Z_UNSTOPPABLE_EAT_GRACE = 0.5
    # 注意：僵尸没有像植物 +0x5C 那样找到「啃食间隔」字段，
    # 所以下面所有僵尸攻速词条都是「等量增伤」——数值等价，但不真的多咬几口。
    Z_LONE_ATKSPD = 0.50        # 孤勇者：+50% 攻速（等量增伤）
    Z_HOMEGUARD_SPEED = 1.00    # 家园卫士：没挨过打时 +100% 移速（= ×2）
    Z_LEGDAY_SPEED = 0.50       # 练腿日：+50% 移速（= ×1.5）
    Z_LEGDAY_IMMUNE_SLOW = True # 练腿日：免疫减速（按未减速的速度算）
    # 冰冻时 Zombie+0x11C（动画速率）会从 1.0 掉到 0.5 左右，用它当减速探针。
    # 落在区间外就当作没被减速，此时免疫逻辑自动退化成「只有 ×1.5」，不会误伤。
    Z_SLOW_ANIM_LO = 0.30
    Z_SLOW_ANIM_HI = 0.98
    Z_ZEALOT_DOUBLE = 0.25
    Z_ZEALOT_SPEED = 0.15       # 狂热者：攻速 +15%（等量增伤）
    Z_SWIFT_SPEED = 0.25        # 灵巧：啃食速度 +25%（等量增伤）

    # ---- v1.0 新增僵尸词条 ----
    Z_ARMORCRASH_RATIO = 1.00   # 装甲冲击：额外伤害 = (头盔+护盾) × 100%
    Z_ARMORCRASH_RANGE = 90     # 装甲冲击：范围半径
    Z_UNYIELDING_ATKSPD = 2.00  # 屹立不倒：血量见底时 +200% 攻速
    Z_UNYIELDING_LIFESTEAL = 0.70   # 屹立不倒：血量见底时 70% 吸血
    Z_SCOPE_DPS = 100.0         # 万用瞄准镜：模拟啃食 DPS
    Z_SCOPE_REACH_1 = 160       # 万用瞄准镜：隔 1 格 = 160 像素
    Z_SCOPE_REACH = 240         # 更万用瞄准镜：隔 2 格 = 240 像素
    Z_ASTRAL_HP = 1800          # 星界躯体：额外生命值
    Z_ASTRAL_DMG = 0.30         # 星界躯体：造成伤害 -30%
    Z_BANG_RATIO = 0.50         # 邦！：范围伤害 = 本次啃食 50%
    Z_BANG_RANGE = 90           # 邦！：范围半径
    Z_FLASH_CHARGES = 2         # 闪现向前：可触发次数
    Z_FLASH_DIST = 90           # 闪现向前：越过距离（像素）
    Z_PORCUPINE = 0.20          # 豪猪：反弹 20% 所受伤害

    # ---- v1.6 新增植物词条 ----
    P_BIGPEA_SLOW = 0.875       # 大腕：攻速 -87.5% → 射击间隔 ×1/(1-0.875) = ×8
    P_BIGPEA_DMG = 5.00         # 大腕：伤害 +500%（= ×6，v1.6.4 的 ×2 再 ×3）
    P_BIGPEA_KNOCK = 80         # 大腕：命中击退距离（一格 = 80px，往右推）
    # 大腕的「那一格」用的是草坪真实网格：第 0 格左沿 40px，每格宽 80px。
    # 设计原文是「把命中的那一格的僵尸击退一格」，不是只推挨打的那一只。
    LAWN_XMIN = 40              # 草坪左边界（像素）
    LAWN_CELL_W = 80            # 每格宽（像素）
    LAWN_COLS = 9               # 列数
    LAWN_ROW_H = 100            # 每行高（像素）
    P_BLOODSWORD_RATIO = 0.30   # 花晓之剑：按造成伤害的 30% 回复已损失生命
    P_TITANRESOLVE_PER_HIT = 0.05   # 泰坦的坚决：每次受伤 +5% 免伤
    P_TITANRESOLVE_CAP = 0.75       # 泰坦的坚决：免伤上限 75%
    P_THORNMAIL = 0.50          # 荆棘之甲：反弹 50% 所受伤害
    P_TITAN_COST = 1.00         # 泰坦巨人：种植额外消耗 100% 阳光
    P_TITAN_HP = 2.00           # 泰坦巨人：血量上限 +200%（= ×3）

    # ---- 大肥鱼（移植自融合版究极植物，见 docs/hextech_values.md §大肥鱼）----
    # 原版数据：80000 血 / 无冷却 / 全场自动索敌 / 5×5 格核爆 8000 /
    #           8% 概率全场核爆每格 5 万 / 阳光 500
    P_FISH_HP = 80000           # 上场血量
    P_FISH_DMG = 8000           # 单次核爆伤害（5×5 格）
    P_FISH_RADIUS_X = 200       # 5 格 × 80px / 2
    P_FISH_RADIUS_Y = 250       # 5 行 × 100px / 2
    # 原版写的是「无冷却」，但真按每帧引爆，8000/帧 会瞬间清空整关。
    # 这里给一个可调的出手节奏，0 = 每帧都打（不推荐）。
    P_FISH_INTERVAL = 0.5       # 秒
    P_FISH_NUKE_CHANCE = 0.08   # 每次出手额外触发全场核爆的概率
    P_FISH_NUKE_DMG = 50000     # 全场核爆伤害
    P_FISH_COST = 0             # 抽到时补扣多少阳光（原版成本 500，默认不扣）
    P_GIANTKILLER_PER_10HP = 0.01   # 巨人杀手：僵尸每 10 点最大血 → +1%
    P_GIANTKILLER_CAP = 2.00        # 巨人杀手：增伤上限 +200%

    # ---- v1.6 新增僵尸词条 ----
    Z_GOLIATH_HP = 1.00         # 歌莉娅巨人：血量 +100%（= ×2）
    Z_GOLIATH_DMG = 0.50        # 歌莉娅巨人：伤害 +50%
    Z_MADMAN_SPEED = 1.50       # 科学狂人：移速 +150%
    Z_MADMAN_SCALE = 0.60       # 科学狂人：碰撞盒缩到 60%
    Z_CLOWN_RADIUS = 90         # 小丑学院：爆炸半径
    Z_CLOWN_DMG = 1800          # 小丑学院：爆炸伤害（对植物，基本等于秒杀）
    Z_SMALLKILLER_SPEED = 1.00  # 小小杀手：移速 +100%
    Z_SMALLKILLER_DMG = 0.50    # 小小杀手：伤害 +50%
    Z_SMALLKILLER_SCALE = 0.60  # 小小杀手：碰撞盒缩到 60%
    Z_BARON_SPEED = 0.50        # 男爵之手：全场僵尸移速 +50%
    Z_BARON_DMG = 0.50          # 男爵之手：全场僵尸伤害 +50%

    # 海克斯护盾借用游戏原生护盾槽（Zombie+0xD8/+0xDC/+0xE0）。
    # 1 = 铁门（正面挡豌豆，最像「护盾」）；2 = 梯子；3 = 报纸。
    # 换成别的类型只要改这里，护盾吸收逻辑不用动。
    Z_SHIELD_TYPE = 1

    # ---- 盲盒卡 ----
    # 把某一个卡槽变成「盲盒」：卡面固定，种下去的是随机植物。
    # 原理：卡面在关卡开始时由 SeedPacket::SetPacketType 定死（0x489B50），
    # 之后只有 mPacketType(+0x34) 会被 Board::AddPlant 读走，
    # 所以改 +0x34 只影响「种什么」，不影响「画什么」。
    BLINDBOX_ON = True
    BLINDBOX_SLOT = 0           # 0 起；-1 = 最后一格
    # "charge"：每次充能完毕随机一次（推荐，卡面/光标预览稳定）
    # "use"   ：每次种下后立刻随机（更盲，拿起时看到的是下一次的）
    # "always"：一直随机（最盲，但光标预览会每帧变）
    BLINDBOX_MODE = "charge"
    # 随机池：全部 49 种。想排除特定植物就删掉对应 ID。
    BLINDBOX_POOL = tuple(range(49))
    BLINDBOX_EXCLUDE = ()       # 例如 (16, 19, 43) 排除需要水面的

    # ---- 抽卡强制（调试 / 想直接玩某条词条时用）----
    # 例：Engine.HEX_FORCE = ("p_deepfish",)  → 场上每株植物都带大肥鱼
    # 只对 scope 匹配的对象生效，写植物词条不会串到僵尸身上。
    HEX_FORCE = ()

    def __init__(self, game, seed=None, preset=None):
        self.g = game
        self.rng = random.Random(seed)
        # preset: {(kind, addr): [hex_id, ...]}，命中就不随机抽。
        # 测试用：避免「先随机抽一帧、再覆盖」把世界状态改掉
        # （例如随机抽到闪现向前会把僵尸推走 90px）。
        self._preset = dict(preset) if preset else {}
        self.plant_state = {}       # addr -> ObjState
        self.zombie_state = {}
        self.objects = []           # 最近一帧的对象快照（含 hexes）
        self.events = []            # 最近一帧产生的事件（给 UI 飘字用）
        self.wave = 0
        self.enabled = True
        self.last_tick = 0.0
        self._prev_zombie = set()
        self.count_mode = COUNT_MODE      # "random" | "wave"
        self.stats = dict(plant=0, zombie=0, hits=0, kills=0)
        self._aura = {}             # 本帧向日葵光环：addr -> (伤害倍率, 免伤比例)
        self._lonely = {}           # 本帧孤勇者判定：addr -> bool
        self._baron = False         # 本帧是否有僵尸带「男爵之手」
        self.seeds = []             # 最近一帧的卡槽快照
        self._blind = {}            # packet addr -> dict(uses=, ready=, type=, rolls=)
        self.blindbox = None        # 本帧盲盒卡槽（dict）或 None

    # ------------------------------------------------ 抽卡
    def _count_for(self, wave=None):
        if self.count_mode == "wave" and wave:
            return hex_count_for_wave(wave)
        return self.rng.randint(HEX_MIN, HEX_MAX)

    def _ensure_hexes(self, objs, state_map, kind, wave=None):
        alive = set()
        for o in objs:
            a = o["addr"]
            alive.add(a)
            st = state_map.get(a)
            if st is None:
                fixed = self._preset.get((kind, a))
                if fixed is None and self.HEX_FORCE:
                    forced = [h for h in self.HEX_FORCE
                              if h in HEX_BY_ID and HEX_BY_ID[h]["scope"] == kind]
                    if forced:
                        fixed = forced
                if fixed is not None:
                    hexes = list(fixed)
                else:
                    count = self._count_for(wave)
                    hexes = roll_hexes(kind, count, o.get("type"), self.rng)
                st = ObjState(hexes, o.get("hp_max") or o["hp"])
                state_map[a] = st
                self.events.append((kind, a, "spawn", hexes))
                # 泰坦巨人：抽到的瞬间就收第二份阳光
                if kind == "plant" and "p_titan" in hexes:
                    self._titan_charge(o, st)
                # 大肥鱼：抽到的瞬间血量顶到 8 万
                if kind == "plant" and "p_deepfish" in hexes:
                    self._fish_charge(o, st)
            o["hexes"] = st.hexes
            o["state"] = st
        # 清理已消失的对象
        for a in [a for a in state_map if a not in alive]:
            del state_map[a]

    # ------------------------------------------------ 主循环
    def tick(self):
        now = time.time()
        dt = now - self.last_tick if self.last_tick else 1 / 30.0
        dt = min(max(dt, 0.001), 0.25)
        self.last_tick = now
        self.events.clear()

        w = self.g.wave()
        if w is None:
            self.objects = []
            return
        self.wave = w

        plants = self.g.scan_plants()
        zombies = self.g.scan_zombies()

        # 上一帧还在、这一帧没了 —— 多半是被打死了，先结算击杀奖励
        cur_addrs = {o["addr"] for o in zombies}
        for a in self._prev_zombie:
            if a not in cur_addrs:
                st = self.zombie_state.get(a)
                if st is not None and "killed" not in st.fired:
                    st.fired.add("killed")
                    self.stats["kills"] += 1
                    self._on_zombie_killed(st, a)
        self._prev_zombie = cur_addrs

        self._ensure_hexes(plants, self.plant_state, "plant", w)
        self._ensure_hexes(zombies, self.zombie_state, "zombie", w)
        self.stats["plant"] = len(plants)
        self.stats["zombie"] = len(zombies)

        self._tick_blindbox(now)

        if not self.enabled:
            self.objects = plants + zombies
            return

        self._aura = self._aura_mods(plants)
        self._lonely = {z["addr"]: self._is_lonely(z, zombies) for z in zombies}
        # 男爵之手：场上只要有一只带这词条的僵尸，全体僵尸一起吃光环
        self._baron = any(z["state"].has("z_baron") for z in zombies)
        self._tick_plants(plants, zombies, now, dt)
        self._tick_zombies(zombies, plants, now, dt)

        self.objects = plants + zombies

    # ------------------------------------------------ 盲盒卡
    def _blindbox_pool(self):
        pool = [t for t in self.BLINDBOX_POOL if t not in self.BLINDBOX_EXCLUDE]
        return pool or list(self.BLINDBOX_POOL)

    def _blind_roll(self, box):
        """给这张卡换一种植物。只写 mPacketType，不动卡面。"""
        pool = self._blindbox_pool()
        cur = box["type"]
        cand = [t for t in pool if t != cur] or pool
        t = self.rng.choice(cand)
        if t != cur:
            self.g.write_i32(box["addr"] + SEED_TYPE, t)
            box["type"] = t
            # 抽到变身茄子必须给个模仿目标，否则种下去是个空壳
            if t == 48:
                other = [x for x in pool if x != 48]
                if other:
                    self.g.write_i32(box["addr"] + SEED_IMITATER,
                                     self.rng.choice(other))
        st = self._blind.get(box["addr"])
        if st is not None:
            st["rolls"] += 1
            st["cur"] = t
        return t

    def _tick_blindbox(self, now):
        """把指定卡槽变成盲盒：卡面固定，种下去随机。

        为什么改 +0x34 不会连带改卡面：卡面是关卡开始时
        SeedPacket::SetPacketType(0x489B50) 一次性建好 reanim，
        之后 Board::AddPlant 只读 mPacketType 决定种什么，
        收卡冷却（0x488EC0）也不会重建卡面。
        所以「画什么」和「种什么」从这一刻起就脱钩了 —— 这就是盲盒。
        """
        self.seeds = []
        self.blindbox = None
        if not self.BLINDBOX_ON:
            self._blind.clear()
            return
        scan = getattr(self.g, "scan_seedbank", None)
        if scan is None:
            return
        seeds = scan()
        if not seeds:
            # 不在关卡里 / 结构对不上：清干净，别拿旧地址乱写
            self._blind.clear()
            return
        self.seeds = seeds

        slot = self.BLINDBOX_SLOT
        if slot < 0:
            slot += len(seeds)
        if not (0 <= slot < len(seeds)):
            return
        box = seeds[slot]
        self.blindbox = box
        addr = box["addr"]
        ready = (box["active"] == 1 and box["cooling"] == 0)

        st = self._blind.get(addr)
        if st is None:
            self._blind[addr] = dict(uses=box["uses"], ready=ready,
                                     base=box["type"], cur=box["type"], rolls=0)
            if ready:
                self._blind_roll(box)
            self.blindbox = box
            return

        mode = self.BLINDBOX_MODE
        used = box["uses"] != st["uses"]
        if mode == "always":
            if ready:
                self._blind_roll(box)
        elif mode == "use":
            if used:
                self._blind_roll(box)
        else:                       # "charge"
            if used or (ready and not st["ready"]):
                self._blind_roll(box)

        st["uses"] = box["uses"]
        st["ready"] = ready
        self.blindbox = box

    # ------------------------------------------------ 向日葵光环
    def _near(self, a, b, rx=None, ry=None):
        rx = self.AURA_RANGE_X if rx is None else rx
        ry = self.AURA_RANGE_Y if ry is None else ry
        return abs(a["x"] - b["x"]) <= rx and abs(a["y"] - b["y"]) <= ry

    def _aura_mods(self, plants):
        """把向日葵的光环汇总成 {植物 addr: (伤害倍率, 免伤比例)}。"""
        suns = [p for p in plants
                if p["type"] in SUN_PLANTS and p.get("state") is not None]
        mods = {}
        for p in plants:
            mult, dr = 1.0, 0.0
            for s in suns:
                if s["addr"] == p["addr"] or not self._near(s, p):
                    continue
                sst = s["state"]
                if sst.has("p_inspire"):
                    mult += self.P_INSPIRE_DMG
                if sst.has("p_windbless"):
                    dr = max(dr, self.P_WIND_DR)
            mods[p["addr"]] = (mult, dr)
        return mods

    # ------------------------------------------------ 植物
    def _tick_plants(self, plants, zombies, now, dt):
        g = self.g
        for p in plants:
            st = p["state"]
            hp = p["hp"]
            mult, dr = self._aura.get(p["addr"], (1.0, 0.0))

            # --- 泰坦巨人：血量上限 ×3（只加一次） ---
            if st.has("p_titan") and not st.titan_hp_done:
                st.titan_hp_done = True
                add = int(st.hp_max * self.P_TITAN_HP)
                if add > 0:
                    st.hp_max += add
                    g.write_i32(p["addr"] + P_HP_MAX, st.hp_max)
                    self._set_plant_hp(p, hp + add)
                    hp = p["hp"]
                    self.events.append(("plant", p["addr"], "泰坦巨人", add))

            # --- 攻速：缩短原生射击间隔 +0x5C，让游戏真的多生成子弹 ---
            self._plant_shoot_apply(p, st)

            # --- 大肥鱼：全场索敌 + 5×5 核爆 ---
            if st.has("p_deepfish"):
                self._deepfish_tick(p, st, zombies, now)

            # --- 山脉龙魂：脱战回盾 ---
            if st.has("p_mountain"):
                if st.shield_max == 0:
                    st.shield_max = int(st.hp_max * self.P_MOUNTAIN_RATIO)
                    st.shield = st.shield_max      # 一上场就带满盾
                elif now - st.last_hit > self.P_MOUNTAIN_REGEN:
                    if st.shield < st.shield_max:
                        if self.P_MOUNTAIN_FULL:
                            st.shield = st.shield_max              # 到点直接回满
                        else:
                            st.shield = min(
                                st.shield_max,
                                st.shield + st.shield_max * self.P_MOUNTAIN_RATE * dt)

            # --- 承受伤害 ---
            if hp < st.hp_prev:
                dmg = st.hp_prev - hp
                st.last_hit = now
                refund = 0.0
                # 会心防御：整段免掉
                if st.has("p_critdef") and self.rng.random() < self.P_CRITDEF:
                    refund = dmg
                    self.events.append(("plant", p["addr"], "会心防御", dmg))
                else:
                    # 风语者祝福：按比例免伤
                    if dr > 0:
                        refund += dmg * dr
                        self.events.append(("plant", p["addr"], "风语者祝福",
                                            dmg * dr))
                    # 泰坦的坚决：按「本次受伤前」已有的层数减伤
                    if st.has("p_titanresolve") and st.resolve_stacks > 0:
                        tr = min(self.P_TITANRESOLVE_CAP,
                                 st.resolve_stacks * self.P_TITANRESOLVE_PER_HIT)
                        part = dmg * tr
                        refund += part
                        self.events.append(("plant", p["addr"], "泰坦的坚决",
                                            part))
                    # 护盾（吃剩下的那部分）
                    if st.shield > 0:
                        absorb = min(st.shield, dmg - refund)
                        if absorb > 0:
                            st.shield -= absorb
                            refund += absorb
                refund = min(refund, dmg)
                if refund > 0:
                    self._set_plant_hp(p, min(st.hp_max, hp + int(round(refund))))

                # 泰坦的坚决：这次受伤让它更硬（从下一次开始生效）
                if st.has("p_titanresolve"):
                    cap = int(round(self.P_TITANRESOLVE_CAP /
                                    self.P_TITANRESOLVE_PER_HIT))
                    if st.resolve_stacks < cap:
                        st.resolve_stacks += 1

                # --- 僵尸侧：谁在啃这株植物 ---
                src_z = self._attribute_zombie(p, zombies)
                if src_z is not None:
                    st.last_src = src_z["addr"]
                    self._zombie_attack(src_z, p, dmg, plants, zombies, now)
                    # 荆棘之甲：把伤害反弹给正在啃它的僵尸
                    if st.has("p_thornmail") and dmg > 0.5:
                        back = dmg * self.P_THORNMAIL
                        self._damage_zombie(src_z, back, source="荆棘之甲")
                        self.events.append(("zombie", src_z["addr"],
                                            "荆棘之甲", back))
            st.hp_prev = p["hp"]

            # --- 全心为你：周围植物将死，向日葵替它去死 ---
            if p["hp"] <= 0 and p["type"] not in SUN_PLANTS:
                self._devotion_rescue(p, plants)

            # --- 守护：每 5 秒给随机一株「别的」植物回血 ---
            if st.has("p_guardian"):
                others = [o for o in plants if o["addr"] != p["addr"]]
                if others and now - st.last_aoe >= self.P_GUARDIAN_CD:
                    st.last_aoe = now
                    tgt = self.rng.choice(others)
                    before = tgt["hp"]
                    self._set_plant_hp(tgt, min(tgt["state"].hp_max,
                                                before + self.P_GUARDIAN_HEAL))
                    if tgt["hp"] > before:
                        self.events.append(("plant", tgt["addr"], "守护",
                                            tgt["hp"] - before))

            # --- 升级献祭 ---
            if st.has("p_sacrifice"):
                if now - st.last_aoe >= self.P_SACRIFICE_CD:
                    st.last_aoe = now
                    dmg = max(1, int(st.hp_max * self.P_SACRIFICE_RATIO))
                    x0 = p["x"]
                    x1 = x0 + self.P_SACRIFICE_RANGE * 80
                    hit_any = False
                    for z in zombies:
                        if abs(z["y"] - p["y"]) >= 60:
                            continue
                        if not (x0 - 20 <= z["x"] <= x1):
                            continue
                        self._damage_zombie(z, dmg, source="sacrifice")
                        hit_any = True
                    if hit_any:
                        g.add_sun(self.P_SACRIFICE_SUN)

    def _plant_shoot_apply(self, p, st):
        """把植物原生射击间隔 +0x5C 按攻速倍率缩短。

        逆向依据（0x45F8A9 起，Plant::UpdateShooting）：
            0x45F8A9  add  dword ptr [esi+0x58], -1     ; 倒计时
            0x45F8B3  jg   跳过
            0x45F8BA  call 0x5af400                   ; rand(0, 15)
            0x45F8BF  mov  ecx, [esi+0x5c]             ; 原始射击间隔
            0x45F8C2  sub  ecx, eax                    ; 间隔 - 抖动
            0x45F8CA  mov  [esi+0x58], ecx             ; 重置倒计时
            0x45F917  call 0x45ef10                    ; ★ 真正生成子弹

        所以改 +0x5C 就是改「每隔多少帧射一发」，子弹由游戏自己生成，
        不是我们补伤害。+0x58 是当前倒计时，不用动。
        """
        mult = 1.0
        if st.has("p_swift"):
            mult += self.P_SWIFT_SPEED
        if st.has("p_zealot"):
            mult += self.P_ZEALOT_SPEED
        if st.has("p_rageblade"):
            mult += min(st.rage_hits * self.P_RAGEBLADE_PER_HIT,
                        self.P_RAGEBLADE_CAP)
        # 大腕：攻速 -87.5% → 间隔 ×8（mult 可以小于 1，这里不再提前 return）
        if st.has("p_bigpea"):
            mult *= (1.0 - self.P_BIGPEA_SLOW)
        if abs(mult - 1.0) <= 1e-6:
            return

        addr = p["addr"] + P_SHOOT_INTERVAL
        if st.shoot_base is None:
            cur = self.g.i32(addr)
            if cur is None or not (1 < cur < 100000):
                return
            st.shoot_base = self._native_shoot_base(cur, mult)
        want = max(self.P_SHOOT_INTERVAL_MIN,
                   int(round(st.shoot_base / mult)))
        cur = self.g.i32(addr)
        if cur != want:
            self.g.write_i32(addr, want)

    def _native_shoot_base(self, cur, mult):
        """从 +0x5C 的当前读数反推「原版间隔」，把倍率一层层剥掉。

        ★ 为什么必须做这一步（v1.6.5 修的 bug）：
        启动器关掉重开时**游戏进程还在跑**，那些已经被我们改过 +0x5C 的植物，
        会把「改后的值」当成原版值读进来，于是再乘一次倍率：

            大腕   150 →(×8) 1200 →(再×8) 9600 → 96 秒一发
            灵巧   150 →(÷1.25) 120 →(再÷1.25) 96 → 76 → 61 ……

        每重启一次启动器就偏一次。往大偏是「大腕不射了」，往小偏是
        「那一行无敌」——正是最危险的方向。

        原版只有 150 / 300 两档间隔，所以落在 [SHOOT_NATIVE_MIN, SHOOT_NATIVE_MAX]
        之外的读数一定是 mod 写的，用 `cur × mult` 就能精确还原（我们写进去的
        就是 `base / mult`）。多剥几层是为了兼容「已经偏了好几次」的存档。
        """
        guess = float(cur)
        for _ in range(4):          # 最多剥 4 层，避免死循环
            if self.SHOOT_NATIVE_MIN <= guess <= self.SHOOT_NATIVE_MAX:
                return int(round(guess))
            if mult <= 0:
                break
            guess *= mult
        # 剥不出来（比如跨版本改过倍率，旧值 600 用新倍率剥会落到 75）：
        # 按原版那两档 150 / 300 就近吸附，比夹到区间边界更接近真值。
        return 150 if guess < 225 else 300

    def _titan_charge(self, p, st):
        """泰坦巨人：种植时额外消耗 100% 阳光。

        游戏在种下去的时候已经扣过一份，这里按 PLANT_COST 再扣一份。
        阳光不够时 add_sun 会夹到 0，不会出现负数。
        """
        if st.titan_sun_done:
            return
        st.titan_sun_done = True
        cost = PLANT_COST.get(p.get("type"), 0)
        if cost <= 0:
            return
        extra = int(round(cost * self.P_TITAN_COST))
        if extra > 0:
            self.g.add_sun(-extra)
            self.events.append(("plant", p["addr"], "泰坦巨人阳光", extra))

    def _fish_charge(self, p, st):
        """大肥鱼：上场就把血量顶到 8 万。

        注意：这里不能走 _set_plant_hp —— 它要读 p["state"]，
        而 _ensure_hexes 里这个调用发生在 o["state"] 赋值之前。
        """
        if st.hp_max >= self.P_FISH_HP:
            return
        st.hp_max = self.P_FISH_HP
        st.hp_prev = self.P_FISH_HP
        p["hp"] = self.P_FISH_HP
        p["hp_max"] = self.P_FISH_HP
        self.g.write_i32(p["addr"] + P_HP_MAX, self.P_FISH_HP)
        self.g.write_i32(p["addr"] + P_HP, self.P_FISH_HP)
        if self.P_FISH_COST:
            self.g.add_sun(-self.P_FISH_COST)
        self.events.append(("plant", p["addr"], "大肥鱼", self.P_FISH_HP))

    def _deepfish_tick(self, p, st, zombies, now):
        """大肥鱼：锁定离房子最近的僵尸，脚下引爆 5×5 核爆。

        ★ 为什么这个能搬、杂交体不能搬：
        融合版的大肥鱼是**新植物**，但它要的行为只有两件事 ——
        「全场找最近的僵尸」和「范围伤害」。前者我们本来就每帧扫全表，
        后者就是血量通道，都不需要新代码路径。
        真正搬不过来的只有外观动画（4 个舞蹈 + 1 个吃饭动作）。
        """
        if not zombies:
            return
        if now - st.fish_last < self.P_FISH_INTERVAL:
            return
        st.fish_last = now

        # 离房子最近 = x 最小（僵尸往左走）
        tgt = min(zombies, key=lambda z: z["x"])
        for z in zombies:
            if abs(z["x"] - tgt["x"]) <= self.P_FISH_RADIUS_X and \
               abs(z["y"] - tgt["y"]) <= self.P_FISH_RADIUS_Y:
                self._damage_zombie(z, self.P_FISH_DMG, source="plant")
        self.events.append(("plant", p["addr"], "大肥鱼", self.P_FISH_DMG))

        if self.rng.random() < self.P_FISH_NUKE_CHANCE:
            for z in list(zombies):
                self._damage_zombie(z, self.P_FISH_NUKE_DMG, source="plant")
            self.events.append(("plant", p["addr"], "全场核爆",
                                self.P_FISH_NUKE_DMG))

    def _devotion_rescue(self, p, plants):
        """全心为你：找一株相邻、还没用过的向日葵替这株植物去死。"""
        for s in plants:
            if s["type"] not in SUN_PLANTS or s["addr"] == p["addr"]:
                continue
            sst = s.get("state")
            if sst is None or not sst.has("p_devotion"):
                continue
            if "devotion" in sst.fired or not self._near(s, p):
                continue
            sst.fired.add("devotion")
            self._set_plant_hp(s, 0)                        # 向日葵替死
            self._set_plant_hp(p, self.P_DEVOTION_HEAL)     # 被救植物回到 100 血
            self.events.append(("plant", p["addr"], "全心为你",
                                self.P_DEVOTION_HEAL))
            return True
        return False

    # ------------------------------------------------ 位移 / 移速
    def _nudge_x(self, z, px):
        """把僵尸水平移动 px 像素（负数 = 朝房子方向）。

        v1.1 起改走原生 float 坐标 Zombie+0x2C。原因：游戏每帧
        `fld [esi+0x2c]` → float→int → `mov [esi+8]`，mX(+0x08) 只是
        每帧被刷新的整数镜像。直接写 mX 会在下一帧被浮点坐标覆盖回去，
        而且整数步进太粗（闪现 90px 会被取整误差吃掉）。
        写 +0x2C 之后 mX 会自己同步；这里顺手补写一次 mX，让本帧
        覆盖层读到的是新坐标，不用等下一帧。
        """
        if abs(px) < 0.01:
            return z["x"]
        fx = self.g.f32(z["addr"] + O_FX)
        if fx is None or not (-1e6 < fx < 1e6) or (abs(fx) < 1e-6 and z["x"]):
            fx = float(z["x"])          # 字段没初始化 / 读不到，用整数坐标兜底
        new = fx + px
        self.g.write_f32(z["addr"] + O_FX, new)
        ni = int(round(new))
        self.g.write_i32(z["addr"] + O_X, ni)
        z["x"] = ni
        return new

    # ------------------------------------------------ 草坪网格
    def _cell_of(self, x):
        """像素 x → 列号（0..8）。越界夹回边界列，不返回 None。"""
        c = int(math.floor((x - self.LAWN_XMIN) / float(self.LAWN_CELL_W)))
        return max(0, min(self.LAWN_COLS - 1, c))

    def _cell_bounds(self, x):
        """像素 x 所在格的左右沿：(col, x0, x1)。右沿不含。"""
        c = self._cell_of(x)
        x0 = self.LAWN_XMIN + c * self.LAWN_CELL_W
        return c, x0, x0 + self.LAWN_CELL_W

    def _knock_cell(self, z, zombies, px, now):
        """把「命中僵尸所在那一格」里的**所有**僵尸一起推开 px 像素。

        设计原文：「可将命中的那一格的僵尸击退一格」——是整格，不是单只。
        同一格 = 同一行（y 差 < 60，和 _attribute_zombie 同口径）+ 同一列
        （x 落在同一个 80px 区间）。只推挨打的那一只，会让「一格」这个说法
        名不副实：一堆僵尸叠在一起时，后面的僵尸照样贴在植物脸上。

        返回被推走的 addr 列表（含命中者自己），方便调用方记事件 / 测试断言。
        """
        _, x0, x1 = self._cell_bounds(z["x"])
        pushed = []
        for o in zombies:
            if abs(o["y"] - z["y"]) >= 60:          # 不同行
                continue
            if not (x0 <= o["x"] < x1):             # 不在同一列
                continue
            ost = o.get("state")
            # 终极不可阻挡：窗口内免疫击退（整格推也推不动它）
            if ost is not None and self._cc_immune(ost, now):
                continue
            self._nudge_x(o, px)
            if ost is not None:
                ost.prev_x = None        # 瞬移了一段，别让移速自适应误判
                ost.chill_prev_x = None
            pushed.append(o["addr"])
        return pushed

    def _speed_bonus(self, z, st, now):
        """当前移速加成（0.5 = +50%，2.0 = ×3），逃跑计划部分随时间衰减。

        四条移速词条是**加法**叠加的（互斥组保证同一只僵尸只会有一条，
        但万一以后放开互斥，加法也比乘法可控）。
        """
        bonus = 0.0
        if st.has("z_lone") and self._lonely.get(z["addr"]):
            bonus += self.Z_LONE_SPEED
        if st.has("z_homeguard") and st.hits == 0:
            # 家园卫士：还没挨过打 → 移速翻倍
            bonus += self.Z_HOMEGUARD_SPEED
        if st.has("z_legday"):
            bonus += self.Z_LEGDAY_SPEED
        if st.has("z_madman"):
            bonus += self.Z_MADMAN_SPEED
        if st.has("z_smallkiller"):
            bonus += self.Z_SMALLKILLER_SPEED
        # 男爵之手：光环，对**所有**僵尸生效（包括男爵自己）
        if self._baron:
            bonus += self.Z_BARON_SPEED
        if "escape" in st.fired:
            left = max(0.0, 1.0 - (now - st.escape_t) / self.Z_ESCAPE_DECAY)
            bonus += self.Z_ESCAPE_SPEED * left
        return bonus

    def _speed_apply(self, z, st, now, dt, plants=()):
        """给僵尸加移速 —— 走「补位移」而不是「改速度字段」。

        ★ 为什么不能直接写 Zombie+0x34（上机实测结论，别改回去）：

        Zombie::Update 里真正的移动段在 0x0052947B：
            0x0052947B  fld  dword ptr [esi+0x34]      ; 当前速度
            0x0052947E  fadd qword ptr [0x679530]      ; + 0.01（斜坡步长）
            0x00529484  fld  dword ptr [esp+8]         ; 目标速度（本帧重算）
            0x00529488  fcom st(1)
            0x00529491  fstp st(1)                     ; 高于目标 → 直接取目标
            0x0052949F  fst  dword ptr [esi+0x34]      ; 写回
            0x005294BC  fstp dword ptr [esi+0x2c]      ; posX += 速度×系数

        也就是每帧都做 speed = min(speed + 0.01, target)，target 由类型/状态
        现算。所以外部把 +0x34 写大，下一帧立刻被夹回 target —— 实测 ×2 完全
        无效、×3 反而更慢（夹回过程占了斜坡）。读报僵尸能提速，是因为它改的是
        游戏自己的状态机（target），外部改不了。

        实测对比（同一只僵尸、用 +0x60 帧钟归一化）：
            基准 ×1        19.5 px/游戏秒
            +0x34 ×2       20.2 px/游戏秒   ← 没变
            +0x34 ×3       12.3 px/游戏秒   ← 更慢

        而 +0x2C 是真正的位置（float），外部写入游戏完全接受、不会回弹
        （实测一次性前移 20px，位移量 20 + 自然 14.2 全部保留），
        +0x08 mX 每帧由它取整同步。所以移速加成 = 每帧补一点位移。

        ★ 为什么用自适应而不是固定系数：
        「+0x34 换算成 px/秒」要乘一个「真正推进位置的 Update 占比」，
        但这个占比不是常数 —— 实测自由行走时约 0.97，僵尸在啃食/停顿时
        只有 0.5 左右。固定系数会让「+100% 移速」实际只有 +50%~+90%。
        所以这里改成观测僵尸自己的真实位移速率当基准：每帧用当前坐标
        减去上一帧补完后的坐标，得到「游戏自己走了多少」，
        再乘 bonus 补回去。这样任何状态下的倍率都准确，
        而且僵尸在啃植物时它自己不动，我们也不推它（符合直觉）。
        """
        bonus = self._speed_bonus(z, st, now)
        immune = self._cc_immune(st, now)
        # 不可阻挡的僵尸即使没有移速词条也要跟踪速率 —— 免疫期间拿它当
        # 「没被控时该走多快」的基准，没有基准就只能靠 +0x34 粗估。
        track = st.has("z_unstoppable")
        # 没有移速词条、也没在跟踪、也不在免疫窗口 —— 一次内存读都不做
        if bonus <= 0 and st.prev_x is None and not immune and not track:
            return

        fx = self.g.f32(z["addr"] + O_FX)
        if fx is None:
            return

        # 终极不可阻挡：不走「观测自己走了多少」那套，直接按原生移速硬推。
        # 因为被冰冻时它压根不动，观测到的速率是 0，按比例补只会补出 0。
        if immune:
            self._cc_immune_move(z, st, bonus, dt, fx, now, plants)
            return

        # 第一次见到：先按 +0x34 估一个初始速率（僵尸朝房子走 = x 减小）
        if st.prev_x is None:
            v = self.g.f32(z["addr"] + Z_SPEED_FIELD) or 0.0
            if not (0.001 < v < self.Z_SPEED_MAX):
                v = self.Z_BASE_SPEED_PX / self.Z_SPEED_HZ / self.Z_MOVE_DUTY
            st.obs_rate = -v * self.Z_SPEED_HZ * self.Z_MOVE_DUTY
            st.prev_x = fx
            return

        # 游戏自己这一帧走了多少。
        # prev_x 记的是「我们上一帧补完之后」的坐标，所以这里不需要再减
        # 我们补的量 —— 补的那部分已经被 prev_x 吸收了，再减一次会把它抵消掉。
        own = fx - st.prev_x
        inst = own / dt if dt > 0 else 0.0
        # 低通：避免单帧抖动放大，同时保留啃食停下的响应
        st.obs_rate += (inst - st.obs_rate) * self.Z_SPEED_SMOOTH

        if bonus <= 0:
            st.prev_x = fx
            return

        rate, k = st.obs_rate, bonus
        if st.has("z_legday") and self.Z_LEGDAY_IMMUNE_SLOW:
            # 免疫减速。被冰冻时 obs_rate 观测到的是「减速后」的速度，
            # 先除以动画速率还原成未减速的速度，再把被减速少走的那部分补回来：
            #   目标总速 = 未减速 × (1+bonus)
            #   需要补 = 未减速 × (1+bonus) − 已走(未减速×ar)
            #          = 未减速 × (1+bonus−ar)
            # ar == 1.0（没被减速）时 k 正好等于 bonus，逻辑自然退化，不会多补。
            ar = self.g.f32(z["addr"] + Z_ANIM_FIELD)
            if ar is not None and self.Z_SLOW_ANIM_LO <= ar < self.Z_SLOW_ANIM_HI:
                rate = st.obs_rate / ar
                k = (1.0 + bonus) - ar

        add = rate * k * dt                 # 同号，朝它自己走的方向补
        st.pending += add
        if abs(st.pending) >= self.Z_SPEED_MIN_STEP:
            step = st.pending
            st.pending = 0.0
            st.prev_x = self._nudge_x(z, step)
        else:
            st.prev_x = fx

    def _cc_immune_move(self, z, st, bonus, dt, fx, now, plants=()):
        """终极不可阻挡：免疫窗口内按「未受控的移速」推进，无视减速 / 冰冻。

        ★ 为什么不复用上面的自适应逻辑：
        那套是「观测僵尸自己走了多少，再按比例补」。僵尸被冻住时它压根不动，
        观测值是 0，补出来还是 0 —— 等于没免疫。所以这里换个思路：
        直接按「它没被控时该走多快」算出期望位移，差多少补多少。

        参考速率取两者较大值：
          - st.obs_rate：进窗口前观测到的真实行走速率（被减速过就偏小）
          - +0x34 换算：游戏自己的目标速度（不受我们影响，是未减速的基准）
        取大的那个，才不会被「触发时正好在减速状态」拉低。

        两个不推的情况：
          - 正在啃植物（`last_bite` 窗口内）：啃食时本来就不动，推了会把它
            从植物嘴边推开。
          - 前面 50px 内有植物：它停住是因为被植物挡住，不是因为被控。
            不判这条的话，被冻住的僵尸会被我们一路推进植物身体里。
        """
        if st.prev_x is None:                   # 第一帧，先记坐标
            st.prev_x = fx
            return
        if now - st.last_bite <= self.Z_UNSTOPPABLE_EAT_GRACE:
            st.prev_x = fx                      # 正在啃，别推
            return
        zx = z["x"]
        for p in plants:
            if abs(p["y"] - z["y"]) < 60 and 0 < zx - p["x"] < 50:
                st.prev_x = fx                  # 被植物挡住，不是被控
                return

        v = self.g.f32(z["addr"] + Z_SPEED_FIELD) or 0.0
        ref = st.obs_rate
        if 0.001 < v < self.Z_SPEED_MAX:
            # +0x34 是「每次 Update 前进多少像素」，乘 100 Hz 就是 px/游戏秒。
            # 这里**不能**再乘 Z_MOVE_DUTY(0.45)：那个系数只是给 obs_rate 的
            # 初始猜测用的（几帧内就被低通纠正），拿它算期望位移会少补一半
            # （普通僵尸 0.23×100=23 px/s，乘 0.45 只剩 10.35）。
            est = -v * self.Z_SPEED_HZ
            if abs(est) > abs(ref):
                ref = est
        if abs(ref) < 1e-3:                     # 完全没有参考值，别乱推
            st.prev_x = fx
            return

        want = ref * dt * (1.0 + max(0.0, bonus))   # 期望位移（朝左为负）
        moved = fx - st.prev_x                      # 上一帧以来游戏自己走的
        deficit = want - moved
        if deficit < -0.01:
            # 单帧最多补 10px，防止速度字段读到脏数据时把僵尸瞬移
            deficit = max(deficit, -10.0)
            st.prev_x = self._nudge_x(z, deficit)
        else:
            st.prev_x = fx

    def _scale_zombie(self, z, st):
        """体型缩小：把碰撞盒 O_W / O_H（+0x10 / +0x14）按比例压小。

        ★ 说明（别当成偷懒）：PVZ 僵尸的**绘制**尺寸由 reanim 动画自带，
        对象里没有一个可直接写的 float 缩放字段 —— 已扫过 .text 里
        disp 落在 0x100~0x170 的全部 float 写入，只有 0x11C（动画速率）是
        每帧会动的，其余都是别的用途。所以这里改的是 GameObject 的宽高，
        影响碰撞 / 命中判定，视觉大小暂时不变。

        每帧维护而不是只写一次：游戏可能在某些状态重算宽高，
        发现被改回去就再写一遍（读 2 次写 2 次，代价很小）。
        """
        scale = (self.Z_SMALLKILLER_SCALE if st.has("z_smallkiller")
                 else self.Z_MADMAN_SCALE)
        if st.base_w <= 0:
            w = self.g.i32(z["addr"] + O_W)
            h = self.g.i32(z["addr"] + O_H)
            if w is None or h is None or w <= 0 or h <= 0:
                return
            st.base_w, st.base_h = w, h
            st.scale_done = True
        tw = max(1, int(st.base_w * scale))
        th = max(1, int(st.base_h * scale))
        if self.g.i32(z["addr"] + O_W) != tw:
            self.g.write_i32(z["addr"] + O_W, tw)
            self.g.write_i32(z["addr"] + O_H, th)
            z["w"], z["h"] = tw, th

    def _cc_immune(self, st, now):
        """终极不可阻挡：当前是否处在「免疫控制」窗口内。

        只判时间，不判词条 —— 窗口只有被词条触发时才会被置上，
        所以没这个词条的僵尸 cc_until 恒为 0，这里永远 False。
        """
        return st.cc_until > now

    def _chill_apply(self, z, st, now, dt):
        """冰寒：把这一帧的实际位移再压掉一部分，实现「减速效果提升」。

        游戏原生的冰冻减速照常生效，这里只做叠加：观测这一帧走了 dx，
        再反向补 `-dx × ratio`，等效于速度再乘 `(1-ratio)`。
        不碰 `+0x34`，原因和移速加成完全一样（每帧会被游戏夹回目标值）。
        """
        if self._cc_immune(st, now):
            # 终极不可阻挡窗口内：不叠减速，并清掉跟踪坐标
            st.chill_prev_x = None
            return
        if now >= st.chill_until or st.chill_ratio <= 0:
            st.chill_prev_x = None
            return
        fx = self.g.f32(z["addr"] + O_FX)
        if fx is None:
            return
        if st.chill_prev_x is None:
            st.chill_prev_x = fx
            return

        dx = fx - st.chill_prev_x
        if abs(dx) < 1e-4:
            st.chill_prev_x = fx
            return

        # dx 为负（正常向左）时补正，dx 为正时反向补，始终是「拖慢」
        new = self._nudge_x(z, -dx * st.chill_ratio)
        st.chill_prev_x = new
        # 移速加成也靠 st.prev_x 观测自身位移，这里必须同步，
        # 否则下一帧会把补减速的位移误当成游戏自己的速度。
        if st.prev_x is not None:
            st.prev_x = new

    # ------------------------------------------------ 僵尸
    def _lost_ratio(self, z, st):
        if not st.hp_max:
            return 0.0
        return max(0.0, min(1.0, 1.0 - z["hp"] / float(st.hp_max)))

    def _zombie_attack(self, z, p, dmg, plants, zombies, now):
        """僵尸这一次啃食的附加效果（全部作用在植物侧）。"""
        st = z["state"]
        extra = 0.0
        tag = None
        st.last_bite = now          # 水龙魂：记下「正在吃植物」

        # 僵尸攻速类词条：啃食间隔字段尚未定位，用等量增伤模拟 DPS
        # （植物侧已改走真实射击间隔 +0x5C，见 _plant_shoot_apply）
        if st.has("z_swift"):
            extra += dmg * self.Z_SWIFT_SPEED
            tag = "灵巧"
        if st.has("z_zealot"):
            extra += dmg * self.Z_ZEALOT_SPEED
            tag = "狂热者"
            if self.rng.random() < self.Z_ZEALOT_DOUBLE:
                extra += dmg
        if st.has("z_lone") and self._lonely.get(z["addr"]):
            extra += dmg * self.Z_LONE_ATKSPD
            tag = tag or "孤勇者"
        if st.has("z_unyielding"):
            lost = self._lost_ratio(z, st)
            extra += dmg * self.Z_UNYIELDING_ATKSPD * lost
            if lost > 0:
                tag = tag or "屹立不倒"
                self._heal_zombie(z, dmg * self.Z_UNYIELDING_LIFESTEAL * lost)
        # ---- v1.6 ----
        if st.has("z_goliath"):
            extra += dmg * self.Z_GOLIATH_DMG
            tag = "歌莉娅巨人"
        if st.has("z_smallkiller"):
            extra += dmg * self.Z_SMALLKILLER_DMG
            tag = "小小杀手"
        # 男爵之手：光环，所有僵尸都吃（含男爵自己）
        if self._baron:
            extra += dmg * self.Z_BARON_DMG
            tag = tag or "男爵之手"
        if st.has("z_heavy"):
            extra += st.hp_max * self.Z_HEAVY_RATIO
            tag = "重量级打手"
        if st.has("z_astral"):
            extra -= dmg * self.Z_ASTRAL_DMG      # 星界躯体：造成伤害 -30%
            tag = tag or "星界躯体"
        if st.has("z_bloodthirst"):
            self._heal_zombie(z, dmg * self.Z_BLOODTHIRST)

        if extra > 0.5:
            self._damage_plant(p, extra)
            self.events.append(("plant", p["addr"], tag, extra))
        elif extra < -0.5:
            self._set_plant_hp(p, p["hp"] - int(round(extra)))   # 少掉的血退回去

        # --- 装甲冲击：第一次进攻，按护甲+护盾造成范围伤害 ---
        if st.has("z_armorcrash") and "armorcrash" not in st.fired:
            st.fired.add("armorcrash")
            base = (z.get("helmet") or 0) + (z.get("shield") or 0)
            burst = base * self.Z_ARMORCRASH_RATIO
            if burst > 1:
                for o in plants:
                    if abs(o["y"] - p["y"]) < 60 and \
                       abs(o["x"] - p["x"]) <= self.Z_ARMORCRASH_RANGE:
                        self._damage_plant(o, burst)
                self.events.append(("plant", p["addr"], "装甲冲击", burst))

        # --- 邦！：每次攻击都带范围伤害 ---
        if st.has("z_bang"):
            splash = dmg * self.Z_BANG_RATIO
            if splash > 0.5:
                for o in plants:
                    if o["addr"] == p["addr"]:
                        continue
                    if abs(o["y"] - p["y"]) < 60 and \
                       abs(o["x"] - p["x"]) <= self.Z_BANG_RANGE:
                        self._damage_plant(o, splash)
                self.events.append(("plant", p["addr"], "邦！", splash))

    def _tick_zombies(self, zombies, plants, now, dt):
        g = self.g
        for z in zombies:
            st = z["state"]
            hp = z["hp"]

            # --- 体型缩小：科学狂人 / 小小杀手 ---
            if st.has("z_madman") or st.has("z_smallkiller"):
                self._scale_zombie(z, st)

            # --- 歌莉娅巨人：血量翻倍（只加一次） ---
            if st.has("z_goliath") and not st.goliath_done:
                st.goliath_done = True
                add = int(st.hp_max * self.Z_GOLIATH_HP)
                if add > 0:
                    st.hp_max += add
                    self.g.write_i32(z["addr"] + Z_HP, hp + add)
                    z["hp"] = hp + add
                    hp = z["hp"]
                    st.hp_prev = hp
                    self.events.append(("zombie", z["addr"], "歌莉娅巨人", add))

            # --- 星界躯体：额外生命值（只加一次） ---
            if st.has("z_astral") and not st.astral:
                st.astral = True
                st.hp_max += self.Z_ASTRAL_HP
                self.g.write_i32(z["addr"] + Z_HP, hp + self.Z_ASTRAL_HP)
                z["hp"] = hp + self.Z_ASTRAL_HP
                hp = z["hp"]
                st.hp_prev = hp
                self.events.append(("zombie", z["addr"], "星界躯体",
                                    self.Z_ASTRAL_HP))

            # --- 护盾被打也算「受到伤害」（否则完全回复 = 每帧回满 = 无敌）---
            prev_shield = st.shield
            cur_shield = self._armor_read(z)
            if cur_shield < prev_shield - 0.5:
                st.last_hit = now

            # --- 山脉龙魂：原生护盾 + 脱战回盾 ---
            if st.has("z_mountain"):
                if st.shield_max == 0:
                    st.shield_max = int(st.hp_max * self.P_MOUNTAIN_RATIO)
                    st.shield = st.shield_max      # 一出场就带满盾
                    self._armor_apply(z)
                elif now - st.last_hit > self.P_MOUNTAIN_REGEN:
                    cur = self._armor_read(z)
                    if cur < st.shield_max:
                        if self.P_MOUNTAIN_FULL:
                            self._armor_write(z, st.shield_max)      # 到点直接回满
                        else:
                            self._armor_write(z, min(
                                st.shield_max,
                                cur + st.shield_max * self.P_MOUNTAIN_RATE * dt))

            # --- 逃跑计划：血量降到 50% 时触发（一次性） ---
            if st.has("z_escape") and "escape" not in st.fired:
                if hp <= st.hp_max * self.Z_ESCAPE_RATIO:
                    st.fired.add("escape")
                    st.escape_t = now
                    st.shield_max = max(st.shield_max,
                                        int(st.hp_max * self.Z_ESCAPE_SHIELD))
                    st.shield = st.shield_max
                    self._armor_apply(z, force=True)
                    self.events.append(("zombie", z["addr"], "逃跑计划", 0))

            # --- 逃跑计划护盾 5 秒内衰减到 0 ---
            if "escape" in st.fired and st.shield_max > 0:
                cur = self._armor_read(z)
                if cur > 0:
                    self._armor_write(
                        z, max(0, cur - st.shield_max / self.Z_ESCAPE_DECAY * dt))

            # --- 死之舞流血 ---
            if st.dot_rate > 0:
                if now < st.dot_until:
                    hp = self._damage_zombie(z, st.dot_rate * dt, source="dot",
                                             silent=True)
                else:
                    st.dot_rate = 0.0

            # --- 承受伤害 ---
            if hp < st.hp_prev:
                dmg = st.hp_prev - hp
                st.last_hit = now
                st.hits += 1

                # 终极不可阻挡：首次受伤 → 开启 10 秒免疫控制窗口（一次性）
                if st.has("z_unstoppable") and "unstoppable" not in st.fired:
                    st.fired.add("unstoppable")
                    st.cc_until = now + self.Z_UNSTOPPABLE_TIME
                    st.chill_until = 0.0        # 立刻甩掉身上已有的减速
                    st.chill_ratio = 0.0
                    st.chill_prev_x = None
                    self.events.append(("zombie", z["addr"], "终极不可阻挡",
                                        self.Z_UNSTOPPABLE_TIME))

                # 先算植物侧的增伤（含鼓舞光环），再一起走减伤
                src = self._attribute(z, plants)
                bonus, tag = 0.0, None
                if src is not None:
                    st.last_src = src["addr"]
                    mult = self._aura.get(src["addr"], (1.0, 0.0))[0]
                    bonus, tag = self._plant_bonus(src, z, dmg, mult)
                    # 冰寒：减速植物命中后，额外压一段移速
                    # （终极不可阻挡的窗口内直接不吃，控制类效果全免）
                    if src["type"] in SLOW_PLANTS and \
                       src["state"].has("p_chill") and not st.has("z_legday") \
                       and not self._cc_immune(st, now):
                        st.chill_until = now + self.P_CHILL_TIME
                        st.chill_ratio = self.P_CHILL_EXTRA
                        st.chill_prev_x = None
                        self.events.append(("zombie", z["addr"], "冰寒", 0))
                total = float(dmg) + bonus
                net = total

                # 孤勇者：9 宫格没别的僵尸 → 50% 免伤
                if st.has("z_lone") and self._lonely.get(z["addr"]):
                    net *= (1 - self.Z_LONE_DR)
                # 泰坦的坚决：按「本次受伤前」已有的层数减伤（从下一次开始变硬）
                if st.has("z_titanresolve") and st.resolve_stacks > 0:
                    tr = min(self.Z_TITANRESOLVE_CAP,
                             st.resolve_stacks * self.Z_TITANRESOLVE_PER_HIT)
                    net *= (1 - tr)
                    self.events.append(("zombie", z["addr"], "泰坦的坚决",
                                        total * tr))
                # 死之舞：40% 转成流血
                if st.has("z_deathdance") and net > 0:
                    deferred = net * self.Z_DEATHDANCE_RATIO
                    net -= deferred
                    st.dot_rate = st.dot_rate + deferred / self.Z_DEATHDANCE_TIME
                    st.dot_until = now + self.Z_DEATHDANCE_TIME
                # 注：护盾不在这里结算 —— 用的是游戏原生 +0xDC，由游戏自己吸收。
                # 所以这里的 dmg 已经是「穿盾之后的伤害」。

                if abs(net - dmg) > 0.5:
                    # hp 已经是「游戏扣完血之后」的值，目标值 = hp + dmg - net
                    self._set_zombie_hp(z, hp + int(round(dmg - net)))
                    if bonus > 0.5 and tag:
                        self.events.append(("zombie", z["addr"], tag, bonus))

                # 泰坦的坚决：这次受伤让它更硬（从下一次开始生效）
                if st.has("z_titanresolve"):
                    cap = int(round(self.Z_TITANRESOLVE_CAP /
                                    self.Z_TITANRESOLVE_PER_HIT))
                    if st.resolve_stacks < cap:
                        st.resolve_stacks += 1

                # 豪猪：反弹 20% 所受伤害给攻击它的植物
                if st.has("z_porcupine") and src is not None and dmg > 0.5:
                    back = dmg * self.Z_PORCUPINE
                    self._damage_plant(src, back)
                    self.events.append(("plant", src["addr"], "豪猪", back))

                if src is not None:
                    self._plant_after_hit(src, z, dmg, now, zombies)
            # 关键：必须用「最终写进内存的值」，否则下一帧会把自己造成的
            # 增伤当成一次新的命中，35% 增伤会被反复叠乘成无限伤害。
            st.hp_prev = z["hp"]

            # --- 水龙魂：受伤后 / 正在啃植物时，每秒回 20 血 ---
            self._waterdragon_regen(z, st, now, dt)

            # --- 移速加成：每帧补位移到 Zombie+0x2C ---
            # 不能写速度字段 +0x34，游戏每帧会把它夹回目标值（见 _speed_apply）
            self._speed_apply(z, st, now, dt, plants)
            # --- 冰寒：在原生减速之上再额外压低移速 ---
            self._chill_apply(z, st, now, dt)

            # --- 闪现向前：越过当前植物，最多 2 次 ---
            if st.has("z_flash"):
                if st.flash_left is None:
                    st.flash_left = self.Z_FLASH_CHARGES
                if st.flash_left > 0:
                    near = [o for o in plants
                            if abs(o["y"] - z["y"]) < 60
                            and 0 <= z["x"] - o["x"] <= 70]
                    if near:
                        st.flash_left -= 1
                        self._nudge_x(z, -self.Z_FLASH_DIST)
                        st.prev_x = None     # 瞬移 90px，别让它污染移速观测
                        st.chill_prev_x = None
                        self.events.append(("zombie", z["addr"], "闪现向前",
                                            self.Z_FLASH_DIST))

            # --- 万用瞄准镜：隔 1 格 / 更万用瞄准镜：隔 2 格 ---
            if (st.has("z_scope") or st.has("z_scope2")) and plants:
                has_close = any(abs(o["y"] - z["y"]) < 60
                                and 0 <= z["x"] - o["x"] <= 70
                                for o in plants)
                if not has_close:
                    reach = (self.Z_SCOPE_REACH if st.has("z_scope2")
                             else self.Z_SCOPE_REACH_1)
                    tgt = None
                    for o in plants:
                        if abs(o["y"] - z["y"]) >= 60:
                            continue
                        d = z["x"] - o["x"]
                        if 70 < d <= reach:
                            if tgt is None or o["x"] > tgt["x"]:
                                tgt = o
                    if tgt is not None:
                        st.scope_acc += self.Z_SCOPE_DPS * dt
                        bite = int(st.scope_acc)
                        if bite:
                            st.scope_acc -= bite
                            self._damage_plant(tgt, bite)

            # --- 击杀结算 ---
            if z["hp"] <= 0 and "killed" not in st.fired:
                st.fired.add("killed")
                self.stats["kills"] += 1
                # 小丑学院：死在原地也要炸一下
                if st.has("z_clowncollege") and "clown" not in st.fired:
                    st.fired.add("clown")
                    self._clown_burst(z, plants)
                self._on_zombie_killed(st, z["addr"])

    # ------------------------------------------------ 原生护盾（防具）
    def _armor_apply(self, z, force=False):
        """把「海克斯护盾」装到僵尸的原生护盾槽上。

        游戏自己的伤害流程（0x53184A）是这样：
            if (zombie+0xD8 != 0 && !(damageFlags & 1))
                damage = TakeShieldDamage(damage);   // 扣 +0xDC，溢出才继续
            if (damageFlags & 2) damage = 原值;
            if (damage > 0) { 扣头盔(+0xD0)；再扣身体(+0xC8) }

        所以只要把 +0xD8 设成非 0、+0xDC/+0xE0 设成血量，
        护盾吸收就完全交给游戏，我们一帧都不用补血。
        """
        st = z["state"]
        cur_type = self.g.i32(z["addr"] + Z_SHIELD_TYPE) or 0
        if force or cur_type == 0:
            self.g.write_i32(z["addr"] + Z_SHIELD_TYPE, self.Z_SHIELD_TYPE)
            z["shield_type"] = self.Z_SHIELD_TYPE
        self.g.write_i32(z["addr"] + Z_SHIELD_MAX, int(st.shield_max))
        self.g.write_i32(z["addr"] + Z_SHIELD, int(st.shield))
        z["shield_max"] = int(st.shield_max)
        z["shield"] = int(st.shield)

    def _armor_read(self, z):
        cur = self.g.i32(z["addr"] + Z_SHIELD)
        if cur is None:
            return 0
        z["shield"] = cur
        z["state"].shield = cur
        return cur

    def _armor_write(self, z, value):
        value = max(0, int(round(value)))
        self.g.write_i32(z["addr"] + Z_SHIELD, value)
        z["shield"] = value
        z["state"].shield = value
        return value

    # ------------------------------------------------ 工具
    def _set_plant_hp(self, p, value):
        value = max(0, min(int(round(value)), p["state"].hp_max))
        self.g.write_i32(p["addr"] + P_HP, value)
        p["hp"] = value
        p["state"].hp_prev = value
        return value

    def _damage_plant(self, p, amount):
        amount = int(round(amount))
        if amount <= 0:
            return p["hp"]
        return self._set_plant_hp(p, p["hp"] - amount)

    def _heal_zombie(self, z, amount):
        st = z["state"]
        if amount < 1:
            return z["hp"]
        new = min(st.hp_max, z["hp"] + int(round(amount)))
        self.g.write_i32(z["addr"] + Z_HP, new)
        z["hp"] = new
        st.hp_prev = new
        return new

    def _waterdragon_regen(self, z, st, now, dt):
        """水龙魂：受伤后 3 秒内、或正在啃植物时，每秒回 20 血。

        两个条件任意一个成立就回血；都不成立（脱战且满血站着）就停。
        用累加器而不是每帧直接加：30 帧/秒下 20 血/秒 = 0.67 血/帧，
        直接 round 会变成每帧 +1，实际变成 30 血/秒。
        """
        if not st.has("z_waterdragon"):
            return
        hurt = (now - st.last_hit) <= self.Z_WATERDRAGON_WINDOW
        biting = (now - st.last_bite) <= self.Z_WATERDRAGON_BITE
        if not (hurt or biting) or z["hp"] >= st.hp_max:
            st.regen_acc = 0.0
            return
        st.regen_acc += self.Z_WATERDRAGON_REGEN * dt
        if st.regen_acc >= 1.0:
            n = int(st.regen_acc)
            st.regen_acc -= n
            self._heal_zombie(z, n)

    def _attribute_zombie(self, p, zombies):
        """反查：谁在啃这株植物（同行、水平距离最近）。"""
        best, best_d = None, 70
        for z in zombies:
            if abs(z["y"] - p["y"]) >= 60:
                continue
            d = abs(z["x"] - p["x"])
            if d < best_d:
                best, best_d = z, d
        return best

    def _clown_burst(self, z, plants):
        """小丑学院：死亡后在原地爆炸，炸周围植物。

        ★ 为什么不是「真的召唤一只小丑僵尸」：
        生成僵尸要走 Board::AddZombie，需要远程调用游戏代码（CreateRemoteThread）
        或手工构造整个 Zombie 对象（含 reanim / 音效 / 状态机初始化），
        失败会直接崩游戏。这里只保留**自爆**这个玩法核心：
        在原位造成一次范围伤害，半径和伤害都是可调的。
        """
        hit = 0
        for p in plants:
            if abs(p["x"] - z["x"]) <= self.Z_CLOWN_RADIUS and \
               abs(p["y"] - z["y"]) <= self.Z_CLOWN_RADIUS:
                self._damage_plant(p, self.Z_CLOWN_DMG)
                hit += 1
        self.events.append(("zombie", z["addr"], "小丑学院", self.Z_CLOWN_DMG))
        return hit

    def _on_zombie_killed(self, st, addr):
        """击杀奖励：收集者。"""
        src = st.last_src
        if not src:
            return
        pst = self.plant_state.get(src)
        if pst is not None and pst.has("p_collector"):
            self.g.add_sun(self.P_COLLECTOR_SUN)
            self.events.append(("zombie", addr, "收集者", self.P_COLLECTOR_SUN))

    def _is_lonely(self, z, zombies):
        for o in zombies:
            if o is z:
                continue
            if abs(o["x"] - z["x"]) < self.Z_LONE_RANGE_X and \
               abs(o["y"] - z["y"]) < self.Z_LONE_RANGE_Y:
                return False
        return True

    def _attribute(self, z, plants):
        """把这次命中归给「同一行、在僵尸左边、最靠右的输出植物」。"""
        best = None
        for p in plants:
            if p["type"] not in OUTPUT_PLANTS:
                continue
            if abs(p["y"] - z["y"]) >= 60:
                continue
            if p["x"] > z["x"] + 20:
                continue
            if best is None or p["x"] > best["x"]:
                best = p
        return best

    def _plant_bonus(self, p, z, dmg, mult=1.0):
        """植物侧增伤（纯计算，不写内存）。返回 (额外伤害, 词条名)。"""
        st = p["state"]
        extra = 0.0
        tag = None
        if mult > 1.0:                      # 鼓舞光环
            extra += dmg * (mult - 1.0)
            tag = "鼓舞"
        if st.has("p_power"):
            extra += dmg * self.P_POWER
            tag = "大力"
        if st.has("p_rageblade"):
            # 攻速已通过 +0x5C 真实生效，这里只负责叠层
            st.rage_hits += 1
        if st.has("p_zealot"):
            # 攻速已通过 +0x5C 真实生效，这里只保留 25% 双倍伤害
            if self.rng.random() < self.P_ZEALOT_DOUBLE:
                extra += dmg
                tag = "狂热者"
        if st.has("p_farsight") and z["x"] < self.P_FARSIGHT_RANGE:
            extra += dmg * self.P_FARSIGHT
            tag = "远射天资"
        # ---- v1.6 ----
        if st.has("p_bigpea"):
            # 大豌豆：伤害翻倍（攻速降低已在 +0x5C 上真实生效）
            extra += dmg * self.P_BIGPEA_DMG
            tag = "大腕"
        if st.has("p_giantkiller"):
            # 每 10 点僵尸最大生命 → +1%，封顶 +200%
            ratio = min(self.P_GIANTKILLER_CAP,
                        (z["state"].hp_max / 10.0) * self.P_GIANTKILLER_PER_10HP)
            extra += dmg * ratio
            tag = tag or "巨人杀手"
        return extra, tag

    def _plant_after_hit(self, p, z, dmg, now, zombies):
        """命中之后的附带效果：电龙魂溅射 / 大腕击退 / 花晓之剑回血。"""
        st = p["state"]

        # --- 大腕：把命中僵尸所在那一格里的所有僵尸往回推一格 ---
        # （+x 是朝右，即远离房子；同格 = 同行 + 同列 80px 区间）
        if st.has("p_bigpea"):
            pushed = self._knock_cell(z, zombies, self.P_BIGPEA_KNOCK, now)
            self.events.append(("zombie", z["addr"], "大腕击退",
                                self.P_BIGPEA_KNOCK))
            if len(pushed) > 1:
                self.events.append(("zombie", z["addr"], "大腕击退整格",
                                    len(pushed)))

        # --- 花晓之剑：按造成伤害的比例回复自身已损失生命 ---
        if st.has("p_bloodsword"):
            lost = st.hp_max - p["hp"]
            if lost > 0:
                heal = min(lost, dmg * self.P_BLOODSWORD_RATIO)
                if heal >= 1:
                    self._set_plant_hp(p, p["hp"] + heal)
                    self.events.append(("plant", p["addr"], "花晓之剑", heal))

        if st.has("p_storm") and now - st.last_aoe >= self.P_STORM_CD:
            st.last_aoe = now
            splash = max(1.0, dmg * self.P_STORM_DMG)
            for o in zombies:
                if o is z:
                    continue
                if abs(o["x"] - z["x"]) <= self.P_STORM_RADIUS and \
                   abs(o["y"] - z["y"]) <= self.P_STORM_RADIUS:
                    self._damage_zombie(o, splash, source="storm")
            self.events.append(("zombie", z["addr"], "电龙魂", splash))

    def _set_zombie_hp(self, z, value):
        value = max(0, min(int(round(value)), z["state"].hp_max))
        self.g.write_i32(z["addr"] + Z_HP, value)
        z["hp"] = value
        z["state"].hp_prev = value
        return value

    def _damage_zombie(self, z, amount, source="plant", silent=False):
        """直接扣僵尸身体血量（无视护甲，因为写的是 +0xC8）。"""
        amount = int(round(amount))
        if amount <= 0:
            return z["hp"]
        addr = z["addr"] + Z_HP
        cur = self.g.i32(addr)
        if cur is None:
            return z["hp"]
        new = max(0, cur - amount)
        self.g.write_i32(addr, new)
        z["hp"] = new
        if z.get("state"):
            z["state"].hp_prev = new
        if not silent:
            self.events.append(("zombie", addr, source, amount))
        return new

    def kill_reward(self, sun=None):
        self.g.add_sun(self.P_COLLECTOR_SUN if sun is None else sun)


# ============================================================ 只读探针
def probe_once(game, seed=None):
    """打一份当前局面快照（不写内存），给命令行 / 调试用。"""
    eng = Engine(game, seed=seed)
    eng.enabled = False
    eng.tick()
    print("关卡 %s   阳光 %s" % (game.level(), game.sun()))
    print("每个植物 / 僵尸随机带 %d~%d 个海克斯（不可重复）\n"
          % (HEX_MIN, HEX_MAX))

    plants = [o for o in eng.objects if o["kind"] == "plant"]
    zombies = [o for o in eng.objects if o["kind"] == "zombie"]

    print("--- 植物 %d 株 ---" % len(plants))
    for p in plants:
        names = "、".join(HEX_BY_ID[h]["name"] for h in p["hexes"])
        print("  [%d] %-8s row=%d col=%d hp=%d/%d  x=%d y=%d  ← %s"
              % (p["idx"], PLANT_NAMES.get(p["type"], "?%d" % p["type"]),
                 p["row"], p["col"], p["hp"], p["state"].hp_max,
                 p["x"], p["y"], names or "（无）"))

    print("\n--- 僵尸 %d 个 ---" % len(zombies))
    for z in zombies:
        names = "、".join(HEX_BY_ID[h]["name"] for h in z["hexes"])
        print("  [%d] %-10s hp=%d/%d  x=%d y=%d  ← %s"
              % (z["idx"], ZOMBIE_NAMES.get(z["type"], "?%d" % z["type"]),
                 z["hp"], z["state"].hp_max, z["x"], z["y"], names or "（无）"))
    return eng
