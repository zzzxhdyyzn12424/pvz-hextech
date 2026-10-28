# 唐诗射手 —— 行为逻辑规格

> 目标植物：`peashooter`（豌豆射手槽位）
> 显示名：**唐诗射手**
> 底包：`1.1.0.1051-zh`（`PlantsVsZombies.exe` 3,007,800 字节）
> 状态：**代码已编译通过、离线自测 23/23 全过；未注入、未在游戏内验证**
> （注入被 `src/aob_patterns.h` 四条空特征码阻塞）

---

## 0. 一句话

一个边射击边推敲诗句的豌豆射手。它每开一枪都在想下一个韵脚，想不出来就射偏；
连续射偏十次，它把整袋豌豆倒进嘴里，原地爆炸。

---

## 1. 为什么必须改代码

已实地排查 `main.pak`（2413 个条目）：

| 类别 | 数量 | 能否承载行为逻辑 |
|---|---|---|
| `.png` | 1895 | 否，纯贴图 |
| `.compiled` | 250 | 否，全是 `particles/` 与 `reanim/` 动画数据 |
| `.ogg` | 166 | 否，音频 |
| `.jpg` | 66 | 否，背景图 |
| `.txt` | 21 | 否，20 个字体描述 + 1 个 `LawnStrings.txt` |
| `.xml` | 1 | 否，`properties/resources.xml` 资源索引 |
| `.mo3` / `.au` / `.gif` / `.js` / `.swf` | 14 | 否 |

**结论**：pak 内没有任何植物数值或行为定义文件。
伤害、射速、空枪概率、僵尸速度、爆炸逻辑**全部硬编码在 `PlantsVsZombies.exe` 里**。
替换资源只能换外观和文字，改不了行为。

`PeaShooter.reanim.compiled` 经解压验证（`magic=0xDEADFED4`，zlib，解压后 141468 字节）
内容为 `anim_full_idle` 等动画轨道，与行为无关。

所以本文档描述的实现路径是 **DLL 注入 + 运行时挂钩**，属于仓库原有 HANDOFF 里
「P5：行为魔改（后续，不在本工具内）」的范围，现在是第一次正式开始做。

---

## 2. 状态机

```
         ┌──────────────┐
         │   NORMAL     │  正常射击，每次射击掷骰
         └──────┬───────┘
                │ miss_count += 1
                │ (命中则 miss_count = 0)
                ▼
        miss_count >= 10 ?
                │
                ├── 否 → 回到 NORMAL
                └── 是 → ┌──────────────┐
                          │    RAGE      │  持续 1.5s
                          │ 射速 20发/秒  │
                          │    全散射     │
                          └──────┬───────┘
                                 │ 计时结束
                                 ▼
                          ┌──────────────┐
                          │   DETONATE   │  半径 1.5 格，1800 伤害
                          │  自身死亡     │  留下绿色残渣
                          └──────────────┘
                                 │
                                 ▼
                          全场僵尸 FEAR 3s
```

### 2.1 常量表

| 常量 | 值 | 说明 |
|---|---|---|
| `EMPTY_CHANCE` | `0.40` | 空枪概率 |
| `DAMAGE_MULT` | `3` | 伤害倍率（在原版 20 基础上 ⇒ 60） |
| `LAND_AHEAD_PX` | `25` | 空枪落点在目标僵尸前方多少 px |
| `DEFAULT_LAND_PX` | `110` | 本行无僵尸时的默认飞行距离 |
| `MIN_LAND_PX` | `45` | 空枪最小飞行距离（防止砸自己脸） |
| `MAX_LAND_PX` | `850` | 空枪最大飞行距离（防止飞出屏） |
| `ARC_PEAK_PX` | `34.0` | 抛物线弧顶抬高，实际 ±15% 抖动 |
| `MISS_THRESHOLD` | `10` | 触发红温的连续未命中次数 |
| `RAGE_DURATION` | `1500` ms | 红温持续时间 |
| `RAGE_FIRE_RATE` | `20` 发/秒 | 红温期间射速 |
| `EXPLODE_RADIUS` | `1.5` 格 | 自爆半径（格宽 80px ⇒ 120px） |
| `EXPLODE_DAMAGE` | `1800` | 自爆伤害 |
| `FEAR_DURATION` | `3000` ms | 僵尸逃跑持续时间 |
| `FEAR_SPEED_MULT` | `10.0` | 僵尸逃跑速度倍率 |

---

## 3. 三个子功能

### 3.1 空枪：抛物线提前落地（NORMAL 状态）

> **2026-10 改版**：最初设计的是"偏航"（绕 z 轴旋转速度向量，子弹斜着直线飞）。
> 用户看完第一版后改成：**子弹走抛物线，在碰到僵尸之前就砸落到地上**。
> 理由：偏航的直线斜飞在原版视角下几乎看不出来，而"飞到一半掉下去"
> 是一眼就能认出的行为。这个改动同时废掉了对 `mVelY` 旋转的依赖。

#### 为什么必须自己积分

原版豌豆是**匀速直线**：每帧 `mX += mVelX`，而 `mVelY` 恒为 0。
游戏里没有任何重力积分。所以想让豆子划弧，只有一条路：
**钩 `Pea::Update`，在它算完之后自己把 `mY` 改成弧线上的位置**。

关键顺序：必须**后置**覆盖（调完原函数再写 `mY`），
因为你无法确定原版 `Pea::Update` 内部会不会自己写 `mY`。
如果只是前置设置 `mVelY` 再指望游戏帮你积分，大概率什么都不发生。

#### 物理模型（`ballistic.cpp`）

逐帧离散积分，不是连续解析解：

```
vy += gravity      每帧
y  += vy
```

给定起点 `(spawnX, startY)`、落点 `(landX, groundY)`、弧顶抬高 `arcPeak`：

```
T  = |landX - spawnX| / |vx|        飞行帧数
dy = groundY - startY               （y 向下为正，通常 > 0）
A  = -arcPeak - dy                  相对起点的总位移
gravity = -2A / T²                  取顶点在 T/2 处
vy0     = -gravity * T / 2
```

推导要点：离散积分下 `Σ_{k=1..T} k = T(T+1)/2`。先定"顶点落在 T/2"这个约束，
得到 `vy0 = -gravity*T/2`，代回位移式解出 `gravity = -2A/T²`。
这个解在离散下顶点会略偏，但视觉上完全看不出来。

保底钳制：
- `T < 2` 时强制 `T = 2`（否则公式退化）
- `gravity < 0.05` 时强制 `0.05`（避免"往上飘"的反直觉轨迹）
- `distF <= 8` 时按 8 算（落点就在嘴边时）

#### 落点怎么定（`aim.cpp`）

用户选的策略：**查本行最近的僵尸，让子弹掉在它前方 `LAND_AHEAD_PX` 处**。
不是随机乱飞，而是"瞄了但砸在脚边"。

| 参数 | 值 | 含义 |
|---|---|---|
| `LAND_AHEAD_PX` | 25 | 僵尸前方多少 px 落地 |
| `DEFAULT_LAND_PX` | 110 | 本行没僵尸时的飞行距离 |
| `MIN_LAND_PX` | 45 | 最近也要飞这么远，别糊自己脸上 |
| `MAX_LAND_PX` | 850 | 最远，防飞出屏幕 |
| `ARC_PEAK_PX` | 34.0 | 弧顶抬高，实际用 ±15% 随机 |
| `ZOMBIE_HIT_W` | 28 | 僵尸碰撞半宽 |

边界行为都有确定答案，不返回垃圾值：

| 情况 | 行为 |
|---|---|
| 本行没有僵尸 | 用 `DEFAULT_LAND_PX` |
| 僵尸在植物左边（身后） | 同样用默认距离，否则会往左飞 |
| 僵尸太近（< 45px） | 钳到 `MIN_LAND_PX` |
| 僵尸太远（> 850px） | 钳到屏幕内 |
| 僵尸已死 / 无头 / 在花瓶里 | 跳过 |
| 链表成环 | `MAX_WALK = 512` 上限保护，不会死循环 |

#### 落地判定

三个条件任一满足即落地：

1. `ny >= groundY` —— y 到达地面
2. `pea->mX >= landX` —— 横向已飞过落点
3. `pea->mX > 1200` —— 飞出右边界

落地后贴到地面（`mY = groundY`，`mVelY = 0`）并置 `mDead = true`。

#### 轨迹表（旁挂，不依赖游戏内存）

`g_tracks: unordered_map<Pea*, Track>` + `mutex`。
**刻意不用游戏自己的子弹链表** —— 那个链表的偏移（`Board::mPeaList`）还没核对过，
用旁挂表可以把"物理正确性"和"内存偏移正确性"彻底解耦：
物理算错和偏移读错，是两类完全不同的 bug，不该互相掩盖。

清理时机：`mDead`、`age > MAX_AGE(600)`、落地且主动销毁。

#### 伪代码

```c
// AddPea 钩子
Pea* HookedAddPea(Board* b, int x, int y, int row, int dmg, PlantType t) {
    Pea* pea = OrigAddPea(b, x, y, row, dmg, t);
    if (!pea || t != PT_PEASHOOTER) return pea;

    pea->mDamage = dmg * DAMAGE_MULT;          // 伤害 ×3

    Plant* shooter = GridLookup(b, row, x);    // 坐标反查发射者
    if (Rand01() < EMPTY_CHANCE) {
        int landX, groundY;
        ComputeMissLanding(shooter, b, &landX, &groundY);
        MakeArcing(pea, landX, groundY, 34 * jitter());
        if (shooter) GetExtra(shooter).missCount++;
    } else {
        GetExtra(shooter).missCount = 0;       // 命中就重置连空
    }
    return pea;
}

// Pea::Update 钩子（后置覆盖）
void HookedPeaUpdate(Pea* self, int unk) {
    OrigPeaUpdate(self, unk);
    if (!self || self->mDead) return;
    Ballistic::UpdateAll(/*selfDestruct=*/true);   // 不需要 board 参数
}
```


### 3.2 红温（RAGE 状态）

进入条件：`missCount >= 10`。

```c
void EnterRage(Plant* self) {
    PlantExtra* ex = GetExtra(self);
    ex->state = ST_RAGE;
    ex->rageEndTime = GetTickCount() + RAGE_DURATION;
    ex->nextFireTime = 0;

    PlayReanim(self, "anim_shoot");       // 或新增 angry 轨道
    TintSprite(self, RGB(192, 57, 43));   // 全身涨红
    SpawnParticle("steam", self->x, self->y);
    Speak(self, TEXT_RAGE);               // "推敲！推敲！推敲！"

    // 全场僵尸逃跑
    ForEachZombie(z) {
        if (z->mDead || z->mInVase) continue;
        z->fearUntil = GetTickCount() + FEAR_DURATION;
        z->facing = -z->facing;           // 掉头
    }
}

void UpdateRage(Plant* self) {
    PlantExtra* ex = GetExtra(self);
    if (GetTickCount() >= ex->rageEndTime) {
        Detonate(self);
        return;
    }
    if (GetTickCount() >= ex->nextFireTime) {
        // 红温期改成"全散射"：每个豆子抛物线角度随机 0~180，
        // 满天乱飞，跟常态的"砸脚边"形成对比。
        Pea* pea = SpawnPea(self, 20 * DAMAGE_MULT);
        int landX = self->mX + RandInt(60, 900);
        int groundY = self->mY + CELL_H - 12;
        Ballistic::MakeArcing(pea, landX, groundY, 34.0f * RandomJitter());
        ex->nextFireTime = GetTickCount() + 50;       // 20 发/秒
    }
}
```

**红温持续时间的实现坑**：原版植物的更新频率跟随 100 FPS 的游戏循环，
用 `GetTickCount()` 计时够用；但如果游戏被暂停（菜单），`GetTickCount` 仍在走，
会导致暂停回来瞬间爆炸。正确做法是读游戏的帧计数器或 `mApp->mUpdateCount`。

### 3.3 自爆与僵尸逃跑

```c
void Detonate(Plant* self) {
    float cx = self->x + 40;   // 格子中心
    float cy = self->y + 40;

    // 范围伤害
    ForEachZombie(z) {
        if (Distance(z, cx, cy) < EXPLODE_RADIUS * 80) {
            DamageZombie(z, EXPLODE_DAMAGE, DMG_EXPLODE);
        }
    }
    SpawnParticle("PeaSplat", cx, cy);
    SpawnParticle("Planting", cx, cy);
    KillPlant(self);

    // 留一滩绿水（纯视觉）
    SpawnEffect("green_puddle", cx, cy, 8000);
}
```

**僵尸逃跑**：核心是把 `Zombie::mSpeed` 乘 10 并翻转朝向。
PVZ 的僵尸速度字段在 `Zombie` 对象里，但每个僵尸的基准速度写死在
`Zombie::UpdateZombie` 的分支里（`mZombieType` 决定）。所以更稳的做法是
**不改变字段，而是在 `Zombie::UpdateZombie` 入口挂钩**：

```c
void __thiscall HookedUpdateZombie(Zombie* z) {
    ZombieExtra* x = GetZExtra(z);
    if (x->fearUntil > Now()) {
        int savedSpeed = z->mSpeed;
        int savedFacing = z->mFacing;
        z->mSpeed = savedSpeed * FEAR_SPEED_MULT;
        z->mFacing = -savedFacing;          // 反向 = 向左走 = 逃出屏幕
        OriginalUpdateZombie(z);
        z->mSpeed = savedSpeed;
        z->mFacing = savedFacing;
        return;
    }
    OriginalUpdateZombie(z);
}
```

这里是**必须先验证**的关键假设：PVZ 僵尸向右走是 `mFacing = +1` 还是 `-1`，
需要实测确认。翻转后僵尸会走出左边界，游戏会在 `mHasHead == false` 之类条件清理，
或者需要额外把僵尸标记为"逃跑中"，避免被计为胜利或卡住。

---

## 4. 挂钩点定位

PVZ 是 32 位 PE，无导出符号，函数地址靠 **字节特征码扫描** 定位。

### 4.1 待定位的三个函数

| 逻辑名 | 作用 | 定位思路 |
|---|---|---|
| `Plant::Fire` | 植物发射子弹 | 从豌豆射手的动画帧触发点反向找 |
| `Board::AddPea` | 生成豌豆对象 | 特征码：调用 `operator new` + 写入速度常量 |
| `Zombie::UpdateZombie` | 僵尸每帧更新 | 特征码：读 `mZombieType` 做跳表分发 |

### 4.2 扫描方法

```c
// behavior/src/aob.h
// 在可执行段内做通配特征码扫描
void* FindPattern(const char* sig, const char* mask, void* start, size_t len);
```

推荐流程：

1. 用 **Cheat Engine** 手动找一版 PVZ 的豌豆发射函数地址（社区已有公开表）
2. 在 CE 里看反汇编，抄下机器码前后 20 字节
3. 把可变部分换成 `?`，得到特征码
4. 写进 `aob_patterns.h`，运行时扫描

**这一步必须真机做，我无法在没有游戏进程的调试器状态下凭空写死地址。**

### 4.3 现有可用的定位线索

已确认的运行时事实：

- `PlantsVsZombies.exe` = 3,007,800 字节，`main.pak` = 25,020,085 字节（打补丁后）
- 游戏窗口客户区固定 `800x600`，格子 `80x100`
- 植物对象里有 `mPlantRow` / `mPlantCol`（0-based）
- 僵尸朝向字段 `mFacing`：**待实测确认 ±1 语义**

---

## 5. 数据传递

每个 `Plant` 对象需要 3 个额外字段，`Zombie` 需要 1 个。有两种做法：

| 方案 | 优点 | 缺点 |
|---|---|---|
| **A. 旁挂表** `std::unordered_map<void*, PlantExtra>` | 不动原对象内存布局，安全 | 每帧 hash 查找，100 FPS × 60 植物可接受 |
| **B. 扩展对象内存** | 快 | 要改 `operator new` 的分配大小，风险高 |

**选 A**。每帧查找成本可忽略（植物最多 60 个）。

```c
struct PlantExtra {
    int   state;          // ST_NORMAL / ST_RAGE
    int   missCount;
    DWORD rageEndTime;
    DWORD nextFireTime;
};

struct ZombieExtra {
    DWORD fearUntil;
};
```

对象生命周期的清理：植物被铲掉/被吃时，`KillPlant` 会释放对象。
必须在 `Plant::~Plant` 挂钩里从 map 移除，否则指针复用会导致状态串味。

---

## 6. 注入方式

```
PlantsVsZombies.exe
    └── 加载 davinci.dll / bass.dll ...（游戏自带）
            └── 【新增】tangshi.dll  ← 本次产物
```

### 6.1 两个可选路径

**路径 A：独立注入器**
写一个 `tangshi_inject.exe`，`CreateProcess(CREATE_SUSPENDED)` +
`VirtualAllocEx` + `WriteProcessMemory` + `CreateRemoteThread(LoadLibrary)`。

- 优点：不改游戏文件，用户可选装
- 缺点：每次启动都要注入；部分杀软报警

**路径 B：DLL 代理劫持**
把 `tangshi.dll` 改名成游戏加载的某个 DLL（如 `gdi42.dll`），
在导出转发里先 `LoadLibrary` 原 DLL 再加载自己。

- 优点：零配置，双击即生效
- 缺点：要精确复刻原 DLL 的导出表，游戏目录里 `gdi42.dll` 只有 303KB 可能是精简转发层

**先做路径 A**，验证可行后再考虑路径 B。

### 6.2 加载顺序

```
DllMain(DLL_PROCESS_ATTACH)
  ├─ 确认目标模块是 PlantsVsZombies.exe（读不到就 return FALSE）
  ├─ 等游戏主窗口出现（轮询 FindWindow 1s 超时）
  ├─ 扫描特征码，拿到 3 个函数地址
  ├─ 写 jmp 钩子（5 字节相对跳转，注意 E9 + rel32 范围 ±2GB）
  └─ 起一个线程处理热键（F8 开关 / F9 重载配置）
```

---

## 7. 验收标准

全部需要在真机验证，**目前一条都没验证**：

- [ ] 注入后游戏能正常启动，不崩
- [ ] 种下豌豆射手，10 次射击里约 4 次飞出抛物弧
- [ ] 空枪的豌豆飞一段后**砸到地上消失**，不是匀速直飞
- [ ] 落点大致在本行僵尸前方半步（不是毫无规律地乱落）
- [ ] 打中僵尸的豌豆伤害是原版 3 倍（普通僵尸 2 发死而不是 5 发）
- [ ] 连续 10 次未命中后，植物变红并冒蒸汽
- [ ] 红温期间密集吐豆（约 20 发/秒）
- [ ] 1.5 秒后原地爆炸，僵尸掉血
- [ ] 场上僵尸掉头高速逃离
- [ ] 逃跑 3 秒后恢复正常方向和速度
- [ ] 植物被吃/被铲时不崩（哈希表清理正确）
- [ ] 关卡结束/重开时状态不残留

---

## 8. 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 特征码随版本变 | 注入后找不到函数，直接 return | 多版本特征码表 + 失败时安全退出不崩游戏 |
| `Pea::Update` 后置覆盖 `mY` 被原版下一帧写回 | 抛物线看不出弧 | 必须后置覆盖；若仍无效说明原版在别处写 mY，改前置挂钩改写 `mVelY` |
| groundY 估算不准 | 豆子悬空或穿地 | 用 `plant->mY + 88`，离线自测已覆盖 10~850px 七档距离 |
| 僵尸翻转方向搞反 | 僵尸往右加速冲向房子 | 先在 CE 里单步验证 `mFacing` 语义 |
| 爆炸伤害到植物自己 | 误判 | 只对 `Zombie` 链表遍历 |
| 哈希表泄漏 | 长时间游戏后内存涨 | `MAX_AGE=600` 帧强制清理 + `Forget()` |
| 反作弊 | 本作为单机同人，无 | — |

---

## 9. 实现顺序

1. ~~先做空枪（挂钩 Fire，改概率）~~ → 改为挂钩 `AddPea`，省掉一条特征码
2. ~~再加偏航~~ → **改成抛物线**：`ballistic.cpp` + `aim.cpp`，已离线自测 23/23
3. 再加红温状态机（含计时）
4. 最后做爆炸 + 僵尸逃跑（涉及 Zombie 钩子）

**当前阻塞**：`src/aob_patterns.h` 里四条特征码仍是空字符串，必须真机提取后注入才生效。

每一步都能单独验证，不通过就不进入下一步。

---

## 10. 相关工作文件

| 文件 | 作用 |
|---|---|
| `behavior/SPEC.md` | 本文档 |
| `behavior/src/tangshi.cpp` | 主实现（四条挂钩 + 状态机 + 自爆/逃跑） |
| `behavior/src/pvz_types.h` | 游戏对象结构体布局（偏移全部待真机核对） |
| `behavior/src/aob.cpp` | 特征码扫描器 |
| `behavior/src/aob_patterns.h` | **待填**，四条特征码目前都是空串 |
| `behavior/src/ballistic.h/.cpp` | 抛物线物理（离散逐帧积分 + 旁挂轨迹表） |
| `behavior/src/aim.h/.cpp` | 落点计算（查本行最近僵尸） |
| `behavior/src/test_ballistic.cpp` | 离线自测，23 项断言 |
| `behavior/inject/inject.cpp` | 注入器（CreateRemoteThread + LoadLibrary） |
| `behavior/build.bat` | 构建脚本：DLL → 自测 → 注入器 |
| `behavior/fetch_toolchain.py` | 从 TUNA 镜像拉 i686 mingw32 工具链 |

---

## 11. 与美术的关系

行为逻辑和贴图**完全独立**，可以先做任何一个。

- 贴图替换走 `tools/prepare_textures.py` → `tools/build_mod.py` → `main.pak`
- 行为注入走 `behavior/` → `tangshi.dll` + 注入器

但有一处耦合：**红温状态的贴图**。原版豌豆射手没有红温帧，
需要在 `PeaShooter_Head.png` 被吃掉/爆炸时切到 `tangshi_head_angry_cut.png`。
这条路径尚未实现（parts.csv 里还没有红温态条目）。

两者同时生效时，玩家看到的是「唐诗射手」的外观 + 唐诗词行为。
只装贴图不装 DLL，就是一个换了皮的普通豌豆射手（伤害还是原版 20）。
