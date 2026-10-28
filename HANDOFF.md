# AI 交接文件：PVZ 原版换皮项目

最后更新：2026-10-26（第 2 次更新：静态逆向纠正了 gApp 误判）
项目路径：`C:\Users\33854\Documents\默认项目\pvz-mod-kit`
用户语言：中文
当前阶段：**贴图链路已完成；行为链路已定位到真正的卡点原因，工具链升级为静态分析**

---

## ⚠️ 0. 先读这一节：上一版交接里的两处错误结论已被推翻

上一版（同日早些时候）写「`0x006A9EC0` 在这个汉化版里不是 gApp」「网上流传的偏移表对这个版本不可靠」。
**这两条都是错的**，是当前项目卡了三轮的直接原因。已用静态反汇编证伪：

### 0.1 `0x006A9EC0` 就是 gLawnApp —— 铁证

```text
0x0044E8FE   push 0x8C8                  ; 0x8C8 = 2248 = sizeof(LawnApp)
0x0044E903   call 0x0061C130             ; operator new
...
0x0044E969   mov dword ptr [0x6A9EC0], eax   ; ★ gLawnApp = new LawnApp
```

这条 `push 立即数 = 对象大小` → `operator new` → `mov [全局], eax` 是构造函数的教科书形态。
**它就是 `LawnApp::LawnApp`。** 上一版之所以看到「PopCap 字符串」，是把这个全局槽**当成字符串地址**去读了，
而它实际存的是堆上 LawnApp 对象的指针。

### 0.2 LawnApp / Board 偏移与标准 GOTY 1.0.0.1051 完全一致

```text
0x00407CA3   mov dword ptr [ebp+0x8C], esi      ; Board->mApp  = LawnApp
0x00407CAE   mov dword ptr [esi+0x768], ebp     ; LawnApp->mBoard = Board
0x00407CA9   push 0x57000                       ; sizeof(Board) = 356,352
```

两条指令成对出现，一次锁死两个方向。另外：

| 偏移 | 含义 | 证据强度 |
|---|---|---|
| `0x006A9EC0` | `gLawnApp` | ★★★ 构造函数赋值，确定 |
| `LawnApp+0x768` | `mBoard` | ★★★ 成对写入，确定 |
| `LawnApp+0x7F8` | `mGameMode` | ★★★ 被反复和 `0x2B` / `0x33..0x3C` / `0x14` / `0x18` / `0` 比较 |
| `Board+0x08C` | `mApp` | ★★★ 同上 |
| `sizeof(Board)` | `0x57000` | ★★★ `push` 立即数 |
| `sizeof(LawnApp)` | `0x8C8` | ★★★ `push` 立即数 |

**结论：这套偏移表可以用。** 之前"不可靠"的印象是被错误的 gApp 结论连累的。

### 0.3 「三条钩子零命中」的真正原因：模式门，不是特征码错

`0x415D99` 处：

```text
0x00415D99   mov eax, [ebp+0x8C]          ; mApp
0x00415D9F   cmp dword ptr [eax+0x7F8], 0x2B   ; if (mGameMode == 0x2B)
0x00415DA6   jne 0x415DB3
0x00415DA8   mov ecx, [eax+0x81C]
0x00415DAE   call 0x00520B30             ; ← 只有 GameMode==0x2B 才走这条
```

`0x520B30 → 0x520C20 → 0x520AC0` 整条链**被 `mGameMode == 0x2B` 门住**。
冒险模式（`mGameMode == 0`）根本不进这条路径，所以钩子零命中是必然的。
上一版其实记对了这个门（§P5 里那句 `cmp [eax+0x7F8],43 / jne`），只是没意识到它意味着整条链作废。

### 0.4 `0x520AC0` 不是 `Pea::Update`

完整反汇编后的真实行为：

```text
0x00520AC5   mov ecx, [esi+0x34]          ; 取格子索引（低 16 位）
0x00520AD5   movzx eax, cx
0x00520AE5   cmp [eax+0x9C], ecx          ; 校验这个格子确实被"自己"占着
0x00520AF0   cmp ecx, 0x11                ; 对象类型判断
0x00520AFF   cmp [eax+0x5C], edx
0x00520B04   fld [esi+0x28] / fld [esi+0x24]   ; 取 y / x
0x00520B18   call 0x0051F2A0              ; ★ 生成抛射物
```

它是「**校验占位 → 用自己坐标生成一颗子弹**」，即开火逻辑，不是每帧物理积分。

### 0.5 `0x51F2A0` 是 `Board::ProcessXxx(type)` 分发器，不是 AddPea

`ret 0x10`（四参数），`[ebp+0x14]` 与 `0x12` 比较后才走植物分支，分支里才 `call 0x51EA30`。
上一版钩的 `0x51EA30` 正是**它下面的叶子函数**——叶子函数被挂在条件分发之下，
所以"钩了但零命中"是结构决定的。

**教训：不要从"某个函数读了 +0x24/+0x28 两个 float"推断它是物理更新。
先看它被谁调、调用点有没有门、函数自己有没有 `ret N` 的签名线索。**

---

## 0.6 接手后先做这 5 件事

1. 读本文件（**尤其 §0.1–§0.5**）、`behavior/SPEC.md`、`content/*.csv`。
2. **不要再确认底包**。实测就是 `1.0.0.1051-zh`（`config.json` 已更正），
   游戏在 `F:\pvz\Plants_Vs_Zombies_V1.0.0.1051_CN_V2`，
   `work/unpacked` 已解包完成（1576 个 reanim 条目），`main.pak` 已换成改过的版本。
3. **贴图链路已完成，不要重做**（见 §P4.5）。`FAIL 0 WARN 0 INFO 5`，装机正常。
4. 行为链路的下一步是**量偏移，不是猜偏移**：用 `behavior/tools/probe.py`。
5. **不要再靠"注入 DLL + 打钩子"做实验**。先用只读探针把偏移量出来，再决定钩谁。

---

## 1. 用户已经拍板的决策（不要重新问）

- **路线**：PVZ1 原版引擎换皮，不做 Unity 重制。
- **底包**：中文底包，**已确认**为 `1.0.0.1051-zh`（`PlantsVsZombies.exe` 3,007,800 字节，
  ImageBase 0x400000，4 个节，无壳）。此前写的 `1.1.0.1056-zh` 是错的，已改 `config.json`。
- **风格**：类似“招笑版/神人版”的抽象、搞笑、梗密度高；不碰真人肖像、真实声音、敏感政治。
- **发布**：免费同人，不售卖、不内购、不打包原版游戏本体。
- **技术**：分部件 PNG 替换 + `LawnStrings` 文本；行为/数值魔改以后再用 Cheat Engine/汇编，不在当前工具范围内。
- **美术**：AI 生成，先换头，不整株替换。
- **工具包位置**：`C:\Users\33854\Documents\默认项目\pvz-mod-kit`。

---

## 2. 当前仓库状态（重要）

| 项目 | 状态 |
|---|---|
| 贴图工具链 | ✅ 完成，`python -m py_compile` 通过 |
| 假数据全链路测试 | ✅ 扫描 → 替换 → 预览 → 构建 → zip |
| 重复处理不漂移 | ✅ 同一输入两次输出 MD5 一致 |
| UTF-8 / GBK 文本 | ✅ 都验证过，编码与 CRLF 行尾均能保持 |
| `work/unpacked/` | ✅ 已解包，`reanim/` 1576 个条目 |
| 真实底包 `main.pak` | ✅ **已还原为原版**（2026-10-28）：25,020,334 字节 / 2413 条目，md5 `2DFE8F8A132C9C7E93B41B72BB6947B1`，与原版包**逐字节一致** |
| 唐诗射手贴图 | ⛔ **已撤销**（2026-10-28，用户要求）。改过的包备份为 `main.pak.tangshi`；`tangshi.dll` / `tangshi_inject.exe` / `tangshi.log` 已移到 `_tangshi_backup/` |
| `build/main.pak` 自研打包器 | ✅ 已接上 `tools/pvz_pak.py`（XOR 0xF7F7F7F7） |
| 行为 DLL 编译 / 注入 | ✅ 编译 0 warning，`LoadLibraryA` 成功 |
| 行为钩子命中 | ❌ **零命中。原因已查明：模式门，见 §0.3** |
| 静态逆向工具 | ✅ **新增** `behavior/tools/static_re.py`（capstone） |
| 外部只读内存探针 | ✅ **新增** `behavior/tools/probe.py`，自检通过；未在游戏上实跑 |
| `.venv` | ✅ 存在，已装 numpy / pillow / capstone 5.0.7 |
| 自动收集阳光 | ✅ **已开启**（2026-10-28）。exe 单字节补丁 `0x3158F: 0x75→0xEB`，见 §2.2 |

### 2.1 唐诗射手贴图当初只改了这 7 个条目（备查）

对比 `main.pak.orig` 与 `main.pak.tangshi` 的索引，差异**只有**以下 7 条，
其余 2406 条完全一致（条目名、顺序、时间戳都没变）：

| pak 内路径 | 原版大小 | 改版大小 | 改动 |
|---|---:|---:|---|
| `reanim\PeaShooter_Head.png` | 6,753 | 9,490 | 换成唐诗射手头部 |
| `reanim\PeaShooter_Lips.png` | 2,958 | 136 | 清空（透明小图） |
| `reanim\PeaShooter_mouth.png` | 3,324 | 86 | 清空 |
| `reanim\PeaShooter_eyebrow.png` | 642 | 74 | 清空 |
| `reanim\PeaShooter_blink1.png` | 1,555 | 82 | 清空 |
| `reanim\PeaShooter_blink2.png` | 1,460 | 82 | 清空 |
| `qroperties\LawnStrings.txt` | 82,104 | 82,182 | 改名 |

⚠ 注意最后一条的路径是 **`qroperties\`**（q 不是 p），是当初打包时的拼写错误，
所以那次改名其实**没有生效**——游戏读的是 `properties\LawnStrings.txt`。
以后要重新做，路径别写错。

要重做这个换皮，只需按上表重新替换这 6 张图；改过的完整包还在
`main.pak.tangshi`，可以直接拿来对照。

### 2.2 自动收集阳光：单字节补丁（已实测开启）

**结论：整个功能就是改 1 个字节。**

```
0x0043158B   cmp  byte ptr [ebx+0x50], 0     ; +0x50 = "已被收集"标记
0x0043158F   75 08    jne  0x431599          ; ★ 原始：有标记才走已收集分支
0x00431591   push ebx
0x00431592   call 0x430ac0                    ; 没标记 → 下落/停留更新
0x00431599   mov  ecx, ebx
0x0043159B   call 0x430e40                    ; 已收集 → 飞向阳光栏
```

把 `0x0043158F` 的 `0x75`(jne) 改成 `0xEB`(jmp) → **无条件**走已收集分支，
阳光/金币一出现就直接飞进阳光栏。

`0x430ac0` 里能直接看到证据，它是「下落」更新：

```
fld [ebp+0x2c] / fadd [ebp+0x24] / fstp [ebp+0x24]   ; posX += velX
fld [ebp+0x30] / fadd [ebp+0x28] / fstp [ebp+0x28]   ; posY += velY
```

即 **Coin/Sun 对象：+0x24=posX(float)、+0x28=posY(float)、+0x2C=velX、+0x30=velY、+0x50=已收集标记**。
这条比之前 `pvz_types.h` 里猜的布局可靠得多，可以直接用。

#### 偏移来源与可信度

来自开源项目 **PvZ Toolkit**（`lmintlcx/pvztoolkit`，GPL-3.0）的 1.0.0.1051 版本表：

| 字段 | 值 | 本地核对 |
|---|---|---|
| `sun` 偏移 | `0x5560` | ✅ 独立反汇编得到 `add [eax+0x5560], ecx`，吻合 |
| `auto_collected` | `{0x0043158F, 补丁 0xEB, 原值 0x75}` | ✅ 本地 exe 该处正是 `0x75` |
| `unlock_sun_limit` | `{0x00430A23, 补丁 0xEB, 原值 0x7E}` | ✅ 本地 exe 该处正是 `0x7E` |

> ⚠ 注意 HACK 结构体字段顺序是 **地址 / 修改值 / 原始值**（见 `inc/data.h`），
> 别把后两个看反了 —— 看反就会以为「原始值是 0xEB」，从而找不到补丁点。

三处独立吻合 → 这份中文 1.0.0.1051 的 `.text` 与 en 版一致，版本表整份可用。
以后要加别的功能，直接去 pvztoolkit 的 `src/data.cpp` 查 `data_1_0_0_1051_en` 即可，
不用再从头逆向。

#### 工具

`tools/autocollect.py`（本次新增）：

```bash
python tools/autocollect.py status        # 看当前状态（只读）
python tools/autocollect.py on            # 永久开启（自动备份 PlantsVsZombies.exe.orig）
python tools/autocollect.py off           # 从备份还原
python tools/autocollect.py on  --live    # 只改运行中的进程，不动文件
python tools/autocollect.py off --live
```

安全设计：打补丁前先校验该字节**必须**是 `0x75`，不是就拒绝修改（防止打错版本把 exe 弄坏）；
第一次 `on` 自动把原 exe 备份成 `PlantsVsZombies.exe.orig`。

已核对：补丁后 exe 与原版**只差 1 个字节**，其余 3,007,799 字节完全一致。

---

## 3. 文件地图

```text
pvz-mod-kit/
├─ HANDOFF.md                 # 本文件
├─ README.md                  # 用户向说明
├─ config.json                # 本地配置（被 .gitignore 忽略）
├─ config.example.json        # 配置模板
├─ run.bat                    # 推荐入口：UTF-8 + 自动 venv
├─ requirements.txt           # Pillow / numpy
├─ content/
│  ├─ plants.csv              # 植物名、机制、台词、图鉴、status
│  ├─ parts.csv               # AI 图 → reanim 部件映射
│  ├─ texture_map.csv         # 扫描生成，别手改
│  └─ strings_extra.csv       # 额外 LawnStrings 段落
├─ prompts/
│  └─ style.md                # 固定风格、负面词、换头/换身体 prompt
├─ docs/
│  └─ week1.md                # 第一周逐日任务
├─ assets/
│  ├─ raw/                    # 放 AI 原图
│  └─ prepared/               # 预留给后续，可不用
├─ tools/
│  ├─ pvz_common.py           # CSV / 编码 / LawnStrings / zip 公共函数
│  ├─ pvz_pak.py              # main.pak 解包/打包（自己逆向的格式，替代 PopStudio）
│  ├─ scan_textures.py        # 扫描 reanim/*.png
│  ├─ prepare_textures.py     # 抠图、缩放、对齐、覆盖、预览
│  ├─ verify_textures.py      # 体检：前后逐部件量化对比，出 JSON + 拼图
│  ├─ test_verify.py          # 体检工具的对抗测试（6 张已知缺陷图）
│  ├─ test_sheet.py           # 拼图排版压力测试
│  └─ build_mod.py            # 合并文本、校验、生成 main_unpack 和 release
├─ behavior/                  # 行为魔改（DLL 注入）—— 独立于贴图链路
│  ├─ SPEC.md                 # 唐诗射手行为规格（状态机、物理、验收）
│  ├─ build.bat               # 一键构建：DLL → 离线自测 → 注入器
│  ├─ fetch_toolchain.py      # 从 TUNA 镜像拉 i686 mingw32 工具链
│  ├─ src/
│  │  ├─ tangshi.cpp          # 主实现：裸钩子 + 状态机 + 运行时偏移自校准 + 日志
│  │  ├─ hook.h               # 跳板钩子（VirtualAlloc near + 指令长度解码器）
│  │  ├─ pvz_types.h          # 游戏对象布局（偏移全部待核对）
│  │  ├─ aob.cpp / .h         # 特征码扫描器（FindInImage 扫整个主模块镜像）
│  │  ├─ aob_patterns.h       # ★ 四条特征码：2 条已填、2 条仍空
│  │  ├─ ballistic.h / .cpp   # 抛物线物理（逐帧积分 + 旁挂轨迹表）
│  │  ├─ aim.h / .cpp         # 落点计算
│  │  └─ test_ballistic.cpp   # 离线自测，23 项断言
│  ├─ tools/                  # 取证工具（C++ 产物编译到 C:\pvztc\out）
│  │  ├─ peek.cpp             # 读内存，多视图（bytes/float/u32）
│  │  ├─ disasm.cpp           # 极简 x86 反汇编器
│  │  ├─ findwrite.cpp        # 找写某偏移的指令
│  │  ├─ findcall.cpp         # 找调用某函数的指令
│  │  ├─ aobfind.cpp          # 特征码搜索
│  │  ├─ memscan.py           # 全内存扫「x 递增」候选（慢，21s/轮，慎用）
│  │  ├─ fastpeek.py          # 高频采样局部区域，线性回归找匀速漂移槽
│  │  ├─ colorstat.py         # 截图颜色统计（验证 PrintWindow 是否真截到画面）
│  │  ├─ static_re.py         # ★新增 静态逆向：反汇编 EXE、找 xref、找字符串（capstone）
│  │  └─ probe.py             # ★新增 外部只读内存探针：不注入不挂钩，量偏移用
│  ├─ inject/inject.cpp       # 注入器
│  └─ out/                    # tangshi.dll + tangshi_inject.exe
├─ work/
│  ├─ unpacked/               # 用户解包后的游戏资源（源）
│  ├─ backup/                 # 原始部件备份，脚本自动建
│  └─ downloads/              # PopStudio / CheatEngine 等外部工具
└─ build/
   ├─ main_unpack/            # 完整工作副本，PopStudio 打包用
   ├─ main.pak                # 输出的 pak
   ├─ preview.png
   └─ release/                # main.pak + 说明 + mod_info + zip
```

**只改 `work/unpacked` 和 `content/`，不要手改 `build/main_unpack`。** 每次 `build_mod.py` 都会覆盖它。

**行为魔改（`behavior/`）走完全独立的链路**：它不碰 pak、不碰贴图，产出是一个 DLL 加一个注入器，
运行时往 `PlantsVsZombies.exe` 里挂钩子。两条链路可以分开推进，互不阻塞。

---

## 4. 核心原理（改代码前必须理解）

### 4.1 PVZ1 的 reanim 是分部件 PNG

PVZ1 的植物不是一整张图集，而是很多独立 PNG：

```text
PeaShooter_Head.png
PeaShooter_mouth.png
PeaShooter_Lips.png
PeaShooter_stalk_bottom.png
PeaShooter_stalk_top.png
PeaShooter_backleaf.png
...
```

`.reanim` / `.reanim.compiled` 只记录每个部件的位置、缩放、旋转关键帧。  
**只要 PNG 画布尺寸不变，替换内容不会破坏原动画。**  
纯换皮不需要改 `.reanim` 或 `.reanim.compiled`。

### 4.2 对齐方式

`prepare_textures.py` 的默认逻辑：

1. 用 `work/backup` 里的**原始部件**计算 alpha 包围盒。
2. AI 图去背景、裁掉透明边。
3. 按原始包围盒缩放。
4. 把 AI 图的中心对齐到原始包围盒中心。
5. 画到同样尺寸的透明画布上，覆盖目标 PNG。

`mode=full` 时改为填满整个画布，适合坚果、土豆雷这种整体替换。  
`scale` 控制大小，`offset_x/y` 控制像素偏移。

### 4.3 变体部件必须一起换

这些植物有多个状态图，只换主图会在攻击/受伤/眨眼时露出原版：

- 向日葵：`SunFlower_head.png`、`SunFlower_head_sing1..5.png`、`SunFlower_head_wink.png`
- 坚果墙：`Wallnut_body.png`、`Wallnut_cracked1.png`、`Wallnut_cracked2.png`
- 豌豆射手：`PeaShooter_Head.png`、`PeaShooter_mouth.png`、`PeaShooter_Lips.png`、blink 图
- 土豆雷：`PotatoMine_body.png`、`PotatoMine_eyes.png`、`PotatoMine_mashed.png`

在 `content/texture_map.csv` 里搜前缀，把所有需要的变体都加进 `parts.csv`。

### 4.4 LawnStrings

- 植物名：`[PEASHOOTER]`
- 图鉴：`[PEASHOOTER_DESCRIPTION]`
- 脚本会自动把 `slot` 转大写，并替换/追加这两个段。
- 中文底包通常是 GBK；英文底包通常是 UTF-8。
- `read_text_auto()` 会保留原编码，只有原文件带 BOM 才写 BOM。
- 行尾会保持原文件风格（CRLF/LF），不要改成硬编码 `\n`。

### 4.5 PopStudio 打包

PopStudio 的 Pak → Pack 需要一个以 `_unpack` 结尾的文件夹。  
本工具故意把 staging 命名为：

```text
build/main_unpack
```

所以对它执行 Pack，输出会自动是：

```text
build/main.pak
```

PopStudio 的 ConsoleProject 是交互式的，不适合直接命令行一键打包。默认 `pack_command` 为空，用 GUI。

---

## 5. 数据表 schema

### 5.1 `content/plants.csv`

一行一个植物槽位。

| 列 | 说明 |
|---|---|
| `slot` | 原版植物 ID，小写，如 `peashooter` |
| `old_name` | 原版中文名，备注用 |
| `new_name` | 改版名 |
| `cost` / `cooldown` / `damage` / `hp` | 设计值，当前不写进游戏，只作文档 |
| `art_focus` | 先换哪个部件：`Head` / `Body` |
| `behavior` | 计划中的机制，当前不实现 |
| `voice` | 台词 |
| `almanac` | 图鉴正文 |
| `status` | `idea` / `art` / `coded` / `done` / `skip` |

`build_mod.py` 默认只把 `art/coded/done/wip/playable` 写进文本。  
`--all` 会忽略 status，把所有企划都写进去，**只用于预览，不要拿来发布**。

### 5.2 `content/parts.csv`

| 列 | 说明 |
|---|---|
| `plant_id` | 对应 `plants.csv` 的 slot |
| `part_file` | 游戏里的 PNG 文件名，必须和 `texture_map.csv` 完全一致，大小写敏感 |
| `raw_file` | `assets/raw/` 里的 AI 图文件名 |
| `scale` | 1.0 = 填满原始 alpha 包围盒；头建议 1.1～1.4 |
| `offset_x` / `offset_y` | 像素微调，正数向右/向下 |
| `mode` | `center` = 对齐原部件中心；`full` = 填满整个画布 |
| `status` | 备注用；只要 `raw_file` 存在就会处理 |

### 5.3 `content/texture_map.csv`

由 `scan_textures.py` 生成，列：

```text
part_file,width,height,bbox_left,bbox_top,bbox_right,bbox_bottom,plant_guess
```

不要手改。换底包后重跑扫描。

### 5.4 `content/strings_extra.csv`

```csv
key,value
[YOUR_KEY],你的文本
```

用于菜单、戴夫台词、商店等非植物文本。

---

## 6. 标准工作流

### 6.1 准备

```bat
cd /d "C:\Users\33854\Documents\默认项目\pvz-mod-kit"
```

确认 `config.json`：

```json
{
  "base_game": "1.1.0.1056-zh",
  "unpacked_dir": "work/unpacked",
  "staging_dir": "build/main_unpack",
  "output_pak": "build/main.pak",
  "release_dir": "build/release",
  "backup_dir": "work/backup",
  "pack_command": []
}
```

### 6.2 解包

用 PopStudio 解包游戏目录的 `main.pak`，把内容放到：

```text
work/unpacked/
```

必须存在：

```text
work/unpacked/reanim/
work/unpacked/properties/LawnStrings.txt
```

### 6.3 扫描

```bat
run.bat tools\scan_textures.py --unpacked work\unpacked
```

### 6.4 生成第一张图

按 `prompts/style.md` 生成“傻豆射手”的头，保存为：

```text
assets/raw/sha_dou_head.png
```

### 6.5 处理

```bat
run.bat tools\prepare_textures.py --config config.json --only peashooter --preview
```

### 6.5b 体检（不要跳过）

肉眼看 `preview.png` 只能发现"完全跑偏"，看不出"偏移 3px""边缘发毛""填充度多 40%"。
`verify_textures.py` 把这些做成量化指标，逐部件跟备份对比：

```bat
run.bat tools\verify_textures.py --config config.json --report build\verify.json --sheet build\verify.png
```

输出三样东西：

- **终端汇总**：`FAIL / WARN / INFO` 计数 + 逐条明细
- **`build/verify.json`**：机读，可接 CI 或后续脚本
- **`build/verify.png`**：可视化拼图，BEFORE/AFTER + 等级色条，中文标注

退出码 **0 = 无 FAIL，1 = 有 FAIL**，可以直接接进自动化。

检查项与阈值（都在 `tools/verify_textures.py` 顶部，好调）：

| code | 等级 | 含义 | 阈值 |
|---|---|---|---|
| `CANVAS` | FAIL | 画布尺寸变了，动画必错位 | 严格 |
| `EMPTY` | FAIL | 替换后全透明，部件消失 | 严格 |
| `KEY_RESIDUE` | FAIL | 洋红/绿幕残留 | > 8 像素 |
| `MISSING` | FAIL | unpacked 里文件没了 | 严格 |
| `BBOX` | WARN | 图形包围盒偏移 | > 3px |
| `CENTER` | WARN | 图形中心偏移 | > 2.5px |
| `TOO_FAT` / `TOO_THIN` | WARN | 填充度变化 | ±25% |
| `EDGE_CLIP` | WARN | 图形被画布裁切 | 原版不贴边而新版贴边 |
| `SOFT_EDGE` | WARN | 半透明边缘过多，发毛 | > 35% |
| `NOT_YET` / `NOT_REPLACED` | INFO | 尚未替换 | — |

**检测器自己也要被测。** 改完判定逻辑后跑：

```bat
run.bat tools\test_verify.py
```

它用 6 张已知缺陷图（洋红没抠净 / 全透明 / 画布变了 / 图形偏移 / 填满变胖 / 缩太小）
验证每一类都能被抓到，当前 6/6 通过。拼图排版用 `tools\test_sheet.py` 验。

### 6.6 构建

先把 `content/plants.csv` 里 `peashooter` 的 `status` 改成 `art`，然后：

```bat
run.bat tools\build_mod.py --config config.json
```

### 6.7 打包

用 PopStudio 对 `build/main_unpack` 执行 **Pack**，得到：

```text
build/main.pak
```

### 6.8 测试

备份游戏目录原 `main.pak`，把新 `main.pak` 覆盖进去，启动游戏，进 1-1 种豌豆射手。

```powershell
$game = 'F:\pvz\Plants_Vs_Zombies_V1.0.0.1051_CN_V2'
Copy-Item "$game\main.pak" "$game\main.pak.bak" -Force   # 只在第一次
Copy-Item build\main.pak "$game\main.pak" -Force
Start-Process -FilePath "$game\PlantsVsZombies.exe" -WorkingDirectory $game
```

游戏自己就是 800x600 窗口化，**不要动窗口**（见 §9 第 20 条）。
如果启动是全屏，按 §9 第 18 条设注册表 `ScreenMode=0`。

### 6.9 发布

再跑一次 `build_mod.py`，把 `main.pak` 收进 `build/release/`，生成 zip。

---

## 7. 第一里程碑：傻豆射手

目标文件：

```text
PeaShooter_Head.png
PeaShooter_mouth.png（可选，第二步）
```

模板已在 `content/parts.csv`：

```csv
peashooter,PeaShooter_Head.png,sha_dou_head.png,1.15,0,0,center,todo
```

验收标准：

- [ ] 游戏能启动
- [ ] 1-1 能种豌豆射手
- [ ] 头明显变了
- [ ] 待机、射击、被啃时动画正常
- [ ] 没有粉色缺图方块
- [ ] 选卡/图鉴中文正常，不是方块
- [ ] 1-1 到 1-5 不闪退

如果头太小：`scale` 调到 1.3～1.5。  
如果头偏：`offset_x/y` 每次调 5～10 像素。  
如果背景有洋红边：重新出纯洋红底图，或 `--tolerance 60`、`--outline 1`。

---

## 8. 已验证 / 未验证

### 已验证

- 8 个假 reanim 部件的扫描。
- 3 张假 AI 图（洋红底、白底）抠图、缩放、对齐、覆盖。
- `build/preview.png` 前后对比图。
- 完整复制、LawnStrings 更新、release 目录、zip。
- 重复运行 `prepare_textures.py` 不漂移：两次输出 MD5 一致。
- UTF-8 文本无 BOM 保持。
- GBK 文本保持；CRLF 行尾保持。
- `run.bat` 的 UTF-8 环境和参数转发。
- `python -m py_compile` 通过。

#### 2026-xx 补测：`PeaShooter_Head.png` 单部件端到端（真实素材）

用 512×512 洋红底、四周留 12% 安全区的手绘素材，实测替换并做像素级校验：

| 指标 | 原版 | 替换后 | 结论 |
|---|---|---|---|
| 画布 | 70×65 | 70×65 | 不变，尺寸正确 |
| alpha bbox | (0,2,70,65) | (2,2,68,65) | 基本吻合 |
| 完全透明像素 | 1218 | 1060 | 差 13%，可接受 |
| 可见像素 | 3332 | 3490 | 基本一致 |
| 洋红残留 | — | **0** | 抠图干净 |

**结论：抠图 → 缩放 → 对齐 → 覆盖 → 存盘这条链路在真实素材上是对的。**

但这次也暴露了两个真实缺陷，都已修复/记录：

1. **`scale > 1.0` 会溢出画布被硬裁**（已修）。原来 `fit_into` 直接放大后交给
   `composite_at`，超出画布的部分被裁掉，导致四边黑边粗细不一致——实测
   `scale=1.15` 时四边全部贴边，透明像素从原版 1218 掉到 359，头明显变胖。
   现在 `clamp_scale()` 放大后按 bbox 中心裁回 box 尺寸，并且新增
   `edge_touch_report()` 在“原版该边没贴边、新版贴了”时打印警告。
   **`content/parts.csv` 里所有 `1.15` 已改成 `1.00`。**
2. **素材自身必须留白**（设计约束，不是 bug）。图形画到画布边缘时，缩放后必然触边。
   AI 出图时必须要求「四周留 10~15% 安全区、图形不触画布边」。

#### 2026-xx 新增：`verify_textures.py` 体检工具

把"肉眼看对不对"变成量化指标，逐部件跟 `work/backup` 对比。已实测：

- 对 `PeaShooter_Head.png` 真实替换报告 `OK`，指标与原版吻合
  （画布 70×65 不变、bbox (0,2,70,65)→(2,2,68,65)、透明 1218→1060、可见 3332→3490、洋红残留 0）
- 对未替换状态正确报 `NOT_YET`，不误报成问题
- `--report` 出 JSON（含 before/after 全量指标）、`--sheet` 出中文可视化拼图
- 退出码 0/1 可直接接自动化

**检测器自身的对抗测试 `tools/test_verify.py`：6/6 通过**

| 坏图 | 预期 | 实际命中 |
|---|---|---|
| 洋红没抠净 | KEY_RESIDUE | EDGE_CLIP, KEY_RESIDUE, TOO_FAT |
| 全透明 | EMPTY | EMPTY |
| 画布尺寸变了 | CANVAS | BBOX, CANVAS, CENTER |
| 图形偏移 | CENTER/BBOX | BBOX, CENTER |
| 填满变胖 | TOO_FAT | EDGE_CLIP, TOO_FAT |
| 缩太小 | TOO_THIN | BBOX, SOFT_EDGE, TOO_THIN |

开发过程中修掉的两个**真实误报**（记下来，别再犯）：

1. **绿幕检测把绿植物全判成残留**。最初用 `G>140 & R<140 & B<140`，
   结果豌豆射手整颗头被判成 1829 像素"绿幕残留"。绿幕失败的真实特征是
   **接近纯 #00FF00 的饱和绿**，改成 `G>200 & R<60 & B<60` 才准。
2. **alpha=1 的隐形像素污染计数**。Lanczos 缩放在全透明区边缘留下
   `(0,255,0,1)` 这种完全看不见的像素，却被算成"亮绿残留"。
   残留检测现在只看 `alpha >= 16` 的像素。

**教训：检测器的误报比漏报更危险。** 一堆假警告会让人直接忽略整个报告。
每加一条规则都要拿"正常图"验证它不误报。

#### 2026-xx 补测：共享画布部件（重要发现）

`content/texture_map.csv` 里 **89 / 1873 个部件的画布面积是 alpha 包围盒的 2 倍以上**
（最极端 `Zombie_digger_head_eye.png` 是 57×57 画布、bbox 只有 2×2）。

代表：`PeaShooter_Lips.png` = **120×120 画布，图形只占 (85,3)-(115,52)**。

这类部件是 PVZ 的「共享画布 / 锚点偏移」设计，图形画在画布某个角落。
`center` 模式用包围盒中心对齐，替换一张「整张嘴」的图时是**正确**的
（AI 图落在原图形的位置上）；但**绝不能用 `mode=full`**——那会把图铺满
120×120，完全错位。处理这类部件前先查 `texture_map.csv` 的 bbox。

### 未验证（下一步必须做）

- 真实中文底包的 `main.pak` 解包。
- 真实 `reanim/*.png` 文件名是否与模板一致。
- 真实游戏里的贴图显示和动画。
- PopStudio 实际 Pack 后的 `main.pak` 能否启动。
- 中文底包字体是否真的正常显示中文。
- AI 图在 64～128px 下的可读性。

**不要在报告里说“已在游戏里测试通过”，除非真的做了。**

---

## 9. 已知坑 / 风险

1. **路径含中文**：项目路径有“默认项目”。Python 没问题，但某些老工具/PopStudio 可能对中文路径敏感。如果打包或读文件报错，先把项目复制到纯英文路径测试。
2. **`run.bat` 必须是 ASCII + CRLF**。不要用编辑器把它保存成 UTF-8 带中文，cmd 会解析错乱。
3. **不要手改 `build/main_unpack`**。它是构建产物，每次都会被覆盖。
4. **不要删除 `work/backup`**。`prepare_textures.py` 用它做原始几何参考；删了以后重复处理会以已修改图为基准，产生漂移。
5. **文件名大小写敏感**。以 `scan_textures.py` 的输出为准，不要凭记忆写。
6. **变体部件**。只换主图会在受伤/攻击时露原版，见 4.3。
7. **中文底包也要验证字体**。如果仍显示方块，说明这个底包不是真正的中文字体版，或需要字体补丁；不是 `LawnStrings` 编码问题。
8. **`--all` 只用于预览**。正式包不要带未完成企划。
9. **行为/数值没有实现**。`plants.csv` 里的机制只是设计文档，游戏里不会自动生效。
10. **AI 图背景**。优先纯洋红 `#FF00FF`；复杂背景脚本抠不干净。
11. **真人素材**。不要用真人脸/声音，除非有书面授权。
12. **`.venv` 已删除**。首次 `run.bat` 需要联网安装 Pillow/numpy。
13. **`config.json` 被 gitignore**。换机器要重新复制 `config.example.json`。
14. **`assets/raw` 默认被 gitignore**。如果要把 AI 原图纳入版本管理，修改 `.gitignore`。
15. **`scale` 不要乱设大于 1.0**。`scale=1.15` 曾让头部四边全部贴边、透明像素减半，
    头看起来变胖、黑边粗细不一。`clamp_scale()` 现在不会让图被硬切，但**留白仍会丢**。
    需要图形盖住原边缘时，用 `scale=1.02~1.05`，别用 1.15。
16. **89 个部件是共享画布**（画布远大于图形）。`PeaShooter_Lips.png` 图形只在
    120×120 画布的 (85,3)-(115,52)。这类部件只能用 `mode=center`，
    用 `mode=full` 会把图形拉满整张画布，彻底错位。查 `texture_map.csv` 的 bbox 判断。
17. **AI 出图必须留白**。要求「四周留 10~15% 安全区、图形不触画布边缘」，
    否则缩放后必然触边被裁。
18. **窗口化启动——已实测确认的唯一正确做法**。

    关键开关在**注册表**，不是 `partner.xml`：

    ```text
    HKCU\Software\PopCap\PlantsVsZombies
      ScreenMode   (DWORD)  0 = 窗口   1 = 全屏（默认）
      PreferredX   (DWORD)  100
      PreferredY   (DWORD)  100
    ```

    ```powershell
    $g='HKCU:\Software\PopCap\PlantsVsZombies'
    Set-ItemProperty $g -Name ScreenMode -Value 0 -Type DWord
    Set-ItemProperty $g -Name PreferredX -Value 100 -Type DWord
    Set-ItemProperty $g -Name PreferredY -Value 100 -Type DWord
    Start-Process 'F:\pvz\Plants_Vs_Zombies_V1.0.0.1051_CN_V2\PlantsVsZombies.exe' `
      -WorkingDirectory 'F:\pvz\Plants_Vs_Zombies_V1.0.0.1051_CN_V2'
    ```

    **实测成功结果**：`GetWindowRect` = `554,196`，窗口 `806x629`，
    `GetClientRect` = `800x600`，`IsIconic` = False，`style` = `0x96CA0000`。
    `806x629` 正是客户区 800x600 加边框标题栏，就是正常窗口化。

19. **只改 `properties\partner.xml` 的 `DefaultWindowed` 没用**。实测把它从
    `false` 改成 `true` 后仍全屏，因为注册表 `ScreenMode=1` 优先。
    （已回滚成 `false`，备份在 `properties\partner.xml.orig`。）

20. **不要用 `SetWindowLong` 改游戏窗口 style**。实测把 `GWL_STYLE` 从
    原值改成 `0xB4C40000`（保留 `WS_POPUP` 又加 `WS_CAPTION|WS_THICKFRAME`），
    游戏立刻异常并退出；之后 `GetWindowRect` 一直返回 `-32000,-32000`
    （最小化哨兵值），`ClientToScreen` 算出 `-31600,-31700`，
    任何"点击窗口中心"都打到屏幕外。
    **只读**位置用 `GetWindowRect` / `GetClientRect` / `GetWindowLong`，
    窗口交给游戏自己管。

21. **频繁强杀进程会让游戏状态错乱**（出现过标题变 `FATAL ERROR`、
    内存只有 18.5MB 的僵尸窗口，`EnumChildWindows` 读不到任何错误文本）。
    测试时一次只启动一个实例，`Stop-Process` 后至少等 600~800ms 再启动。

22. **Windows 上做跨进程可读的日志，必须用 `CreateFileA` + `FILE_SHARE_*`**。
    `fopen_s(&f, path, "w")` 和 `_fsopen(path, "w", _SH_DENYNO)` **都会独占**，
    进程活着时外部完全读不出来（报「文件正由另一进程使用」）。
    可用形态：
    ```cpp
    static HANDLE h = CreateFileA(path, FILE_APPEND_DATA,
        FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_ALWAYS,
        FILE_ATTRIBUTE_NORMAL, nullptr);
    SetFilePointer(h, 0, nullptr, FILE_END);
    WriteFile(h, buf, n, &written, nullptr);
    CloseHandle(h);
    ```
    （每次开、写、关，不要长期持有句柄。）
    `LogOpen()` 用 `CREATE_ALWAYS` 清空一次。

23. **钩子函数必须是 naked 的**。跳板里抄出来的 x86 指令依赖进入时的寄存器
    （`0x00520AC0` 的 `mov esi,eax` / `mov eax,[edi]` 都用 eax/edi），
    普通 C 函数 prologue 会立刻破坏它们 → 即使钩子被调用，原逻辑也会拿着垃圾指针执行。
    GCC 32 位写法见 §P5 的 `__attribute__((naked))` 代码块。
    注意 naked 函数里**不能有 C++ 对象**，参数只能靠 `pushl` 或寄存器传。

24. **`0x006A9EC0` 在本汉化版里不是 gApp**。读出来 `0x0292A2D8`，
    内容是 `"PopCap"` / `"P=C;LC_PopCap"` 字符串。网上流传的偏移表对这个版本不可靠。

25. **全内存扫描不可行**。`memscan.py` 扫 383MB 需要 **21 秒/轮**，
    而豌豆 2 秒就飞出屏幕。必须先把范围缩到几 KB 再高频采样。

26. **PowerShell 里 `R` 是内置别名**（`Invoke-History`），定义 `function R {...}`
    会被别名优先解析，报「找不到接受实际参数的位置形式参数」。函数名不要用单字母。

27. **PowerShell here-string `@'...'@` 传给 `python -c` 会被吞引号**。
    多行 Python 一律写成 `.py` 文件再调用，不要内联。

28. **`Get-Content` / `Select-Object` 会让 `$LASTEXITCODE` 变 1**，这不是 Python 失败。
    判成功要看实际输出。

29. **截图的两条路都要验证**：
    - `PrintWindow(hwnd, hdc, 0)` **能截到 PvZ 游戏画面**（有效图，93979 种颜色），
      且窗口被遮挡也能截。用 `behavior/tools/colorstat.py` 验证是否全黑/全同色。
    - `CopyFromScreen` 窗口被遮挡时截到的是遮挡物，不可靠。
    但**注意用户的约束**：m01132 明确要求实机验证由用户自己做，不要主动截游戏图。

30. **PowerShell 不支持 `<<` 左移**（报 `The '<' operator is reserved for future use`）。
    `lParam = (y << 16) | x` 这类运算必须包在 `Add-Type` 的 C# 代码块里。

31. **`Add-Type` 用 `System.Drawing.Bitmap` 必须显式引用程序集**：
    `Add-Type ... -ReferencedAssemblies @([System.Drawing.Bitmap].Assembly.Location)`，
    否则报「命名空间 System.Drawing 中不存在类型或命名空间名称 Bitmap」。

32. **`0x6A9EC0` 就是 gApp，别再推翻它。** 上一版读到这里是堆指针就断言"不是 App"，
    实际上构造函数 `push 0x8C8 / call operator new / mov [0x6A9EC0], eax` 写得明明白白。
    **判断"某个全局是不是指针"的正确姿势：看它有没有被 `mov [全局], reg` 写过，
    以及写之前是不是刚 `operator new` 过。**

33. **PVZ 的"更新"是模式分发的，不是统一入口。** `Board::Update` 按 `mGameMode`
    走完全不同的分支（`0x2B` / `0x33..0x3C` / `0` 各一条）。
    **钩任何 `0x520xxx` 之前，先确认它在冒险模式下真的被调。**
    验证方法：`static_re.py refs <地址>` 找到所有引用点，往上追到 `Board::Update` 的门。

34. **`ret N` 是识别调用约定的最快线索。** `ret 0x10` = 四参数，
    `ret 8` = 双参数。上一版把 `0x51F2A0` 当成 AddPea，其实它是四参数的
    `Board::ProcessXxx(type)` 分发器。**函数体里 `[ebp+0x14]` 这种第 5 个栈槽
    被当开关用，基本就是分发器。**

35. **Board 有 0x57000 字节。** 扫 Board 内部时范围给 0x6000 只能扫到头部，
    抛射物/僵尸数组可能在很后面。`probe.py` 的默认 span 已经改成 0x57000。

36. **别用注入 DLL 的方式做"探索性实验"。** 每次注入都要重启游戏，一次失败就丢几分钟，
    还会踩「频繁强杀进程导致状态错乱」（第 21 条）。探索阶段一律用
    `probe.py`（纯外部只读），确认偏移之后才写 DLL。

---

## 10. 下一步任务（按顺序）

### P0：确认真实底包

- 问用户要：中文底包的版本号、`PlantsVsZombies.exe` 版本、PopStudio 是否已安装。
- 让用户解包到 `work/unpacked`。
- 确认 `properties/LawnStrings.txt` 的实际编码（GBK/UTF-8）和行尾。
- 跑 `scan_textures.py`，检查 `PeaShooter_Head.png` 是否存在。

### P1：傻豆射手

- 生成头图，处理，预览，构建，Pack，启动测试。
- 记录实际 `scale` / `offset` 和截图。
- 通过后更新 `plants.csv` 的 `status=art`。

### P2：加 2～3 个简单植物

优先：坚果墙、土豆雷、向日葵。  
每加一个都要跑完整链路，不要一次加 10 个再测试。

### P3：变体与文本

- 补齐 cracked / sing / blink 变体。
- 检查选卡、图鉴、中文显示。
- 需要时用 `strings_extra.csv` 改菜单文本。

### P4：发布素材

- 15 秒短视频：原版 → 换皮 → 空枪/搞笑行为 → 图鉴台词 → 评论区点菜。
- 发布包只包含 `main.pak` + 说明，不包含游戏本体。

### P5：行为魔改 —— 唐诗射手（已开工，卡在钩子命中）

> 2026-10 进展：**贴图已完成并装机**（`tangshi_head_final.png`，见 P4.5）。
> 行为模块：**代码写完、编译通过、离线自测 23/23、DLL 能成功注入**，
> 但**三条钩子全部零命中**——游戏里看不出任何效果。这才是当前真正的卡点。
> 详细规格见 `behavior/SPEC.md`。

**名字变更史**：用户先说「傻豆射手」，一度改成「唐氏射手」（已否决——那是拿真实病患当笑料），
最终定为 **唐诗射手**。机制保留：打不准、红温、自爆、僵尸吓跑。
笑点从"取笑病人"转到"文艺青年射不中"，更安全也更好笑。

**已实现**（`behavior/src/`）：

| 模块 | 文件 | 状态 |
|---|---|---|
| 抛物线空枪 | `ballistic.cpp` / `aim.cpp` | ✅ 离线自测 23/23 |
| 40% 空枪概率 | `tangshi.cpp` | ⚠️ 钩子零命中，从未执行 |
| 伤害 ×3 | `tangshi.cpp` | ⚠️ 同上 |
| 红温状态机 | `tangshi.cpp` | ⚠️ `AOB_PLANT_UPDATE` 仍空 |
| 自爆 + 僵尸逃跑 | `tangshi.cpp` | ⚠️ `AOB_ZOMBIE_UPDATE` 仍空 |
| 日志系统 | `tangshi.cpp` `Log()` | ✅ 可跨进程边跑边读 |
| 运行时偏移自校准 | `tangshi.cpp` `TryCalibrate()` | ✅ 编译通过，未被调用过 |

#### 2026-10 实测：注入成功但三条钩子全部零命中

**已确认可行的部分**（这些不要再重做）：

1. **DLL 编译**：`exit=0`，0 warning，`tangshi.dll` = 748643 字节。
2. **注入链路**：用 `tangshi_inject.exe` 成功，日志出现 `[+] LoadLibraryA 返回 0x62130000 (成功)`。
   在 PowerShell 里直接用 `[W.I]::VirtualAllocEx` 那套会踩 `UIntPtr` 转换坑，别折腾。
3. **跳板钩子字节级正确**（用 `C:\pvztc\out\peek.exe` 验证）：
   - `00520AC0` 首字节变成 `E9 1B 0F C1 61`，指向 DLL 内 `0x621319E0` ✅
   - 跳板 `0A620000` 内容是 `56 8B F0 8B 07 E9 BB 0A F0 F5`，
     原 5 字节完整抄出 + `E9` 回跳 `0x00520AC5`（验算 `0x0A62000A + 0xF5F00ABB` 截断 32 位 = `0x00520AC5`）✅
4. **Win32 日志可跨进程读**（这是踩了三轮才成的，见 §9 第 22 条）。
5. **naked 钩子正确**（GCC 32 位）——普通 C 函数会破坏 `eax`/`edi`，必须 naked：

```cpp
static void* g_peaTramp = nullptr;
extern "C" void Tangshi_OnPeaRefresh(void* self);

__attribute__((naked)) void __stdcall HookedPeaRefreshEntry() {
    __asm__ __volatile__(
        "pushal\n"
        "pushl %eax\n"                  // 进入时的 eax 才是对象指针
        "call _Tangshi_OnPeaRefresh\n"
        "addl $4, %esp\n"
        "popal\n"
        "jmp *g_peaTramp\n"
    );
}
```

**卡点：`[hook] call` 打点从未出现。** 用户打出子弹（m02027「打出子弹了」、m02127「开火了」），
日志里一条都没有。说明这三个地址在这个版本里根本不是豌豆更新路径：

| 尝试过的地址 | 依据 | 结果 |
|---|---|---|
| `0x00520AC0` | 反汇编看到 `fld [esi+0x28]` + `fld [esi+0x24]`，被 `Board::Update` 循环以 `cmp [esi+8],9` 分发调用 | 零命中 |
| `0x0051FCD0` | `findwrite 24 --float` 的命中者 | 零命中 |
| `0x0051EA30` | 同时读写 `+0x24` 和 `+0x28` | 零命中 |
| `0x00520B30` | Board 物件分发循环（SEH 序言） | 未装钩 |

**重读 `0x00520AC0` 完整反汇编后的新结论**（重要，别再当它是 Pea::Update）：
函数体只有 0x64 字节，做的事是「如果这个对象的格子索引非零、取 App+0x820 网格、
格子里的植物指针等于自己、且 `[plant+0x5C] >= (1或2)`，就把 `[esi+0x28]` 和 `[esi+0x24]`
两个 float 经 `0x006397D0`(__ftol) 转 int 后 push 给 `0x0051F2A0`」。
→ 这更像**某类对象的"绘制/注册"辅助**，不是每帧更新的物理积分。**它是被分发调用了但对象类型不符，
或者这条分发链在这个汉化版里根本没走到。**

**下一个 AI 的第一件事（已重写，旧版"先找 App 指针"作废——App 早就找到了）**：

App 和 Board 的偏移已经**量死**（§0.1–§0.2）。现在缺的只有一样：**抛射物在 Board 里的位置**。
按下面做，全是确定性的，不需要再猜：

1. 开游戏，进 1-1，种一棵豌豆射手让它持续开火。
2. 跑 `probe.py dump` —— 确认 `gApp` 非空、`mBoard` 非空、
   `Board+0x8C == gApp` 显示 ✅。**这一步就能证明偏移表对不对**。
3. 跑 `probe.py findpea 6` —— 它会在 Board 的 0x57000 字节里按**行为特征**
   （`+0x24` 每帧稳定递增、量级 0~1000）找出飞行中的豌豆，
   并打印它所在的 `Board+0x?????`。**这个偏移就是抛射物数组的位置。**
4. 拿到偏移后再 `probe.py watch <豌豆指针> 3`，确认 `+0x24/+0x28/+0x2C/+0x30`
   就是 x / y / vx / vy。
5. **只有到这里才开始写钩子。** 而且优先考虑「不挂钩」的方案：
   DLL 起一个 60Hz 线程，读 `gApp → mBoard → 抛射物数组`，直接改 `y`。
   这样根本不存在"钩子零命中"这一类问题。

**关键判断**：如果 `probe.py dump` 里 `Board+0x8C == gApp` 打 ❌，说明 `0x768` 在这个
EXE 上不是 mBoard，才需要回头重新定位——但静态证据已经很强，大概率是 ✅。

**已备好的取证工具**（`behavior/tools/`，产物在 `C:\pvztc\out\`）：

| 工具 | 用途 |
|---|---|
| `peek.exe <pid> <hexaddr> [--float\|--u32\|--bytes] <n>` | 读内存，多视图 |
| `disasm.exe <pid> <hexaddr> <nbytes>` | 极简 x86 反汇编 |
| `findwrite.exe <pid> <off> [--float]` | 找写某偏移的指令 |
| `findcall.exe` | 找调用某函数的指令 |
| `aobfind.exe` | 特征码搜索 |
| `memscan.py <pid> --rounds N` | 全内存扫「x 递增」的候选 float 对 |
| `fastpeek.py <pid> <base> <size> [frames]` | 高频采样指定区域，线性回归找匀速漂移槽 |
| `colorstat.py <png>` | 截图颜色统计（验证 PrintWindow 是否真截到画面） |

**重要教训**：`memscan.py` 全量扫 383MB 需要 **21 秒/轮**，豌豆 2 秒就飞过去了，
**全内存扫描这条路根本不可行**。必须先缩小到几 KB 的可疑区域，再用 `fastpeek.py` 高频采样。

**构建命令**：`cd behavior` 然后 `build.bat`。它做三件事：编译 DLL → **运行离线自测**（失败就停）→ 编译注入器。

#### 部署 + 注入 + 读日志（完整可复制）

```powershell
$gameDir='F:\pvz\Plants_Vs_Zombies_V1.0.0.1051_CN_V2'
Get-Process PlantsVsZombies -EA SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 900          # 少于 800ms 会状态错乱
Copy-Item 'C:\pvztc\out\tangshi.dll' "$gameDir\tangshi.dll" -Force
Remove-Item "$gameDir\tangshi.log" -EA SilentlyContinue
Start-Process "$gameDir\PlantsVsZombies.exe" -WorkingDirectory $gameDir
Start-Sleep -Seconds 7
cd $gameDir; cmd /c "echo. | tangshi_inject.exe 2>&1"
# 注意 InitThread 里等窗口最多 30 秒，注入后要等 30+ 秒才会出现 AOB 那几行
Get-Content "$gameDir\tangshi.log" -Encoding UTF8
```

#### 编译 DLL（PowerShell，注意 `-Wl,--kill-at` 必须加引号）

```powershell
$env:Path='C:\pvztc\mingw32\bin;'+$env:Path
$src='C:\Users\33854\Documents\默认项目\pvz-mod-kit\behavior\src'
& 'C:\pvztc\mingw32\bin\i686-w64-mingw32-g++.exe' -m32 -std=c++17 -O2 -Wall -Wextra `
  -Wno-unused-parameter -fno-use-linker-plugin -static-libgcc -static-libstdc++ `
  -D_CRT_SECURE_NO_WARNINGS -I $src -shared `
  "$src\tangshi.cpp" "$src\aob.cpp" "$src\ballistic.cpp" "$src\aim.cpp" `
  -o 'C:\pvztc\out\tangshi.dll' -static '-Wl,--kill-at' -luser32 -lpsapi
```

不加引号的话 PowerShell 会把 `--kill-at` 拆成 `--kill` 和 `-a`。

### P4.5：唐诗射手头部贴图（已完成）

**成品**：`assets/raw/tangshi_head_final.png`，70×65，可见像素 3339（原版 3304，差 1%），
内容范围 `x=[0,69] y=[2,64]` 与原版**完全相同**，洋红残留 0。

**做法**（`content/parts.csv:2` 已指向它，`scale=1.00`、`mode=center`）：

1. 从 `assets/raw/tangshi_head.png`（1536×1536 洋红底）抠图 → 1455×878。
2. 取 x≥254 的整头（脸+眼+眉+嘴+口水+喇叭口）→ **1201×878**。
3. 直接 `resize((70,63))` 横向拉伸，贴到 70×65 画布 (0,2)。比例 1.368 vs 原版 1.111，横向压缩 19%（略瘦，用户已接受）。
4. 清掉 33 个半透明近洋红噪点：`(r>120)&(b>120)&(g<120)&((min(r,b)-g)>60)&(a<64)`。

**关键结构性认知**（这是绕了很多弯路才搞清的）：
原版 `PeaShooter_Head.png` 70×65 **本身只含椭球头身，不含眼睛和嘴细节**。
眼睛由 `blink1/blink2/eyebrow` 叠加，嘴由 `mouth/Lips` 叠加（`Lips` 是 120×120 共享画布，
图形只在 `(85,3)-(115,52)`）。而 AI 出的图把五官全画进去了，
直接塞进 Head 会变成**四只眼两张嘴**。

→ 解决方式：`parts.csv` 里五个五官层全部改成 `mode=hide`：

```
peashooter,PeaShooter_Head.png,tangshi_head_final.png,1.00,0,0,center,todo
peashooter,PeaShooter_mouth.png,,0,0,0,hide,done
peashooter,PeaShooter_Lips.png,,0,0,0,hide,done
peashooter,PeaShooter_blink1.png,,0,0,0,hide,done
peashooter,PeaShooter_blink2.png,,0,0,0,hide,done
peashooter,PeaShooter_eyebrow.png,,0,0,0,hide,done
```

`mode=hide` 由 `tools/prepare_textures.py` 的 `make_hidden_like(reference)` 实现：
返回**同尺寸**全透明图（不能是 1×1，否则 reanim 锚点错位）。

**体检结果**：`FAIL 0 WARN 0 INFO 5`，exit=0。5 条 INFO 全是 HIDDEN。
`build/main.pak` 25,013,670 字节 / 2413 条目，装机后 md5 `F603B06BF87F5A05945A6613E288E003`。

---

## 11. 和用户协作的规则

- 用户是中文用户，回复用中文。
- 不要重新讨论“要不要 Unity 重制”，已经定了原版换皮。
- 不要一次生成 49 个植物；先做一个可玩 demo。
- 改动前先看 `content/*.csv` 和现有文件，不要覆盖用户手填的内容。
- 报告时说清楚：改了哪个文件、跑了什么命令、验证了什么、没验证什么。
- 不要编造游戏内测试结果。
- 保持免费同人边界：不售卖、不内购、不打包原版游戏。
- **实机验证由用户自己做**（用户 m01132 明确要求）。不要再截游戏窗口的图、不要操作游戏窗口。
  给用户操作步骤和验证清单，让他自己跑。
- **不要把真人群体当梗**。用户曾提出「唐氏射手」，已改成「唐诗射手」并说明理由。

---

## 12. 还需要问用户的问题

1. 中文底包的具体版本/来源是什么？EXE 版本号是多少？
2. 游戏目录和 `main.pak` 在哪里？PopStudio 装了吗？
3. 第一张图用哪个 AI 工具（即梦/豆包/GPT-Image/SD）？需要按工具微调 prompt。
4. 是否要中文配音/TTS？如果要，后续再定音色和台词。
5. 第一版发布平台（B站/抖音/其他）？影响视频比例和标题风格。

---

## 13. 命令速查

```bat
:: 扫描
run.bat tools\scan_textures.py --unpacked work\unpacked

:: 只处理豌豆射手 + 预览
run.bat tools\prepare_textures.py --config config.json --only peashooter --preview

:: 体检（推荐每次都跑，出 JSON + 可视化拼图）
run.bat tools\verify_textures.py --config config.json --report build\verify.json --sheet build\verify.png

:: 只体检某个植物 / 只出汇总不列明细
run.bat tools\verify_textures.py --config config.json --only peashooter --quiet

:: 体检 unpacked 里全部 reanim PNG（不只 parts.csv 登记过的）
run.bat tools\verify_textures.py --config config.json --all

:: 体检工具自己的对抗测试（改判定逻辑后必跑）
run.bat tools\test_verify.py

:: 处理全部已填 raw 的部件
run.bat tools\prepare_textures.py --config config.json

:: 正式构建（只写 art/coded/done）
run.bat tools\build_mod.py --config config.json

:: 预览全部 20 个企划的文本（不要发布）
run.bat tools\build_mod.py --config config.json --all

:: 复用已有 main_unpack，加快迭代
run.bat tools\build_mod.py --config config.json --skip-copy
```

### pak 解包 / 打包（自己写的，不依赖 PopStudio）

```bat
:: 列出 pak 里的条目（解密后解析索引）
python tools\pvz_pak.py list F:\pvz\Plants_Vs_Zombies_V1.0.0.1051_CN_V2\main.pak

:: 解包到 work\unpacked
python tools\pvz_pak.py unpack F:\pvz\Plants_Vs_Zombies_V1.0.0.1051_CN_V2\main.pak -o work\unpacked

:: 打包（build_mod.py 会自动调用，一般不用手动跑）
python tools\pvz_pak.py pack build\main_unpack -o build\main.pak

:: 校验：条目数、索引结束位置、总长、PNG 签名
python tools\pvz_pak.py verify build\main.pak
```

### 行为魔改（唐诗射手）

```bat
cd behavior
build.bat                       :: DLL -> 离线自测 -> 注入器，一步到位

:: 单独跑离线自测
C:\pvztc\out\test_ballistic.exe

:: 注入（先开游戏，进关卡后再跑）
out\tangshi_inject.exe

:: 注入器找不到进程时手动指定
out\tangshi_inject.exe PlantsVsZombies.exe
```

### 静态逆向（不需要游戏在跑，最推荐）

```bat
:: 节表 / ImageBase / 入口
.venv\Scripts\python.exe behavior\tools\static_re.py pe

:: 反汇编任意地址
.venv\Scripts\python.exe behavior\tools\static_re.py dis 0x44E8F0 220

:: 找所有引用某个地址的指令（找全局变量、验证偏移）
.venv\Scripts\python.exe behavior\tools\static_re.py refs 0x6A9EC0

:: 找字符串，以及引用它的代码
.venv\Scripts\python.exe behavior\tools\static_re.py strref "PopCap"

:: 找 call/jmp 到某个函数的指令
.venv\Scripts\python.exe behavior\tools\static_re.py xref 0x520AC0
```

### 外部只读内存探针（不注入 / 不挂钩 / 不写内存）

```bat
:: 自检（不需要游戏）
.venv\Scripts\python.exe behavior\tools\probe.py selftest

:: 确认 gApp / mBoard，dump Board 头部（进关卡后跑）
.venv\Scripts\python.exe behavior\tools\probe.py dump

:: 扫 Board 里的指针数组候选
.venv\Scripts\python.exe behavior\tools\probe.py scan

:: 找飞行中的豌豆（先让豌豆射手开火，再跑）
.venv\Scripts\python.exe behavior\tools\probe.py findpea 6

:: 盯一个对象，看哪个字段在动
.venv\Scripts\python.exe behavior\tools\probe.py watch 0x12345678 3

:: 手动指定进程
.venv\Scripts\python.exe behavior\tools\probe.py --pid 1234 dump
```

### 内存取证（逆向辅助，产物在 C:\pvztc\out）

```bat
:: 读内存：字节 / float / u32
C:\pvztc\out\peek.exe <pid> 0292A2D8 --bytes 32
C:\pvztc\out\peek.exe <pid> 00520AC0 --bytes 24
C:\pvztc\out\peek.exe <pid> <addr> --float 16

:: 反汇编
C:\pvztc\out\disasm.exe <pid> 00520AC0 220

:: 找写某偏移 / 调某函数的指令
C:\pvztc\out\findwrite.exe <pid> 24 --float
C:\pvztc\out\findcall.exe <pid> 00520AC0
```

```bat
:: 全内存扫「x 递增」候选（慢！21 秒/轮，只在前两步都失败时用）
.venv\Scripts\python.exe behavior\tools\memscan.py <pid> --rounds 5

:: 高频采样局部区域（快，先靠静态分析把范围缩到几 KB 再用）
.venv\Scripts\python.exe behavior\tools\fastpeek.py <pid> <base_hex> <size_hex> 40

:: 验证截图是不是真的截到了画面（不是全黑）
.venv\Scripts\python.exe behavior\tools\colorstat.py build\live_now.png
```

**取 pid**：`(Get-Process PlantsVsZombies).Id`

---

## 14. 一句话交接

**两条链路的状态完全不对等：**

**贴图链路 —— 已完成。** `main.pak` 解包 → 换头 → 重新打包 → 真实中文版装机验证通过：
`tangshi_head_final.png`（70×65，可见像素 3339 vs 原版 3304，内容范围与原版完全相同，
洋红残留 0），五个五官层用 `mode=hide` 抠空避免「四只眼两张嘴」，游戏正常启动无 FATAL ERROR。

**行为链路 —— 卡点已查明，不是"钩子不好使"，是"钩错了地方"。**
代码写完、编译 0 warning、离线自测 23/23、DLL 注入成功、跳板字节级正确——
但 `0x520AC0` / `0x51FCD0` / `0x51EA30` 三条钩子零命中。原因见 §0.3：

- `0x520B30 → 0x520C20 → 0x520AC0` 整条链被 `mGameMode == 0x2B` 门住，冒险模式不进；
- `0x520AC0` 本身是"校验占位 → 生成子弹"的开火逻辑，不是 `Pea::Update`；
- `0x51EA30` 是 `0x51F2A0` 这个 `ProcessXxx(type)` 分发器下面的叶子函数，被条件分发挡着。

**下一个 AI 的第一件事（旧版"先找 App 指针"已作废）：**

1. App 和 Board 已经量死：`gLawnApp = 0x6A9EC0`、`LawnApp+0x768 = mBoard`、
   `Board+0x8C = mApp`、`sizeof(Board) = 0x57000`。证据见 §0.1–§0.2，不用再找。
2. 开游戏进 1-1，种豌豆射手开火，跑 `probe.py dump` 确认 `Board+0x8C == gApp` ✅。
3. 跑 `probe.py findpea 6`，按行为特征把**抛射物数组在 Board 里的偏移**量出来。
4. 拿到偏移再写钩子；**优先用 60Hz 轮询线程直接改坐标，不要挂钩**——
   这样从根本上不存在"零命中"这一类问题。

**三条铁律，别踩：**
1. **不要改游戏窗口的 style**（`SetWindowLong`），只用 `SetWindowPos`。改 style 会让进程退出。
2. **实机验证交给用户**（m01132）。不要截游戏窗口的图，也不要瞎猜结果。
3. **Linux/Windows 老工具都怕中文路径**。`ld.exe` 遇到「默认项目」会彻底失效，
   构建产物必须先写 `C:\pvztc\out` 再 copy 回仓库。

**另外三条容易被忽略的：**
4. **钩子函数必须 naked**——跳板抄出的指令依赖进入时的 `eax`/`edi`，普通 C 函数会立刻破坏它们。
5. **日志必须用 `CreateFileA` + `FILE_SHARE_*`**——`fopen`/`_fsopen` 都会独占，进程活着时读不出来。
6. **先用只读探针，再动注入**。`probe.py` 全程 `ReadProcessMemory`，不写内存、不注入，
   理论上不可能搞崩游戏；而每次注入调试都要重启游戏，代价高一个数量级。
