# PVZ 海克斯版

给《植物大战僵尸》加「海克斯强化」词条的同人 mod。每波随机抽 1~3 个词条，
植物和僵尸都会变异，效果直接作用在正在运行的游戏里。

> **本仓库不包含游戏本体。** 你需要自备《植物大战僵尸》1.0.0.1051 中文版。

---

## 直接玩（不用写代码）

1. 去 [Releases](https://github.com/zzzxhdyyzn12424/pvz-hextech/releases) 下载 `PVZ_Hextech_v1.6.7.zip`
2. 解压，**先打开游戏并进入一关**（让场上出现植物和僵尸）
3. 双击 `PVZ海克斯版.exe`

启动器会自己找游戏、校验版本、拉起覆盖层。热键：

| 键 | 作用 |
|---|---|
| `F7` | 血量显示 开 / 关 |
| `F8` | 详情面板 开 / 关 |
| `F9` | 效果结算 暂停 / 恢复（关掉后卡片还在，但不生效） |
| `F10` | 重新抽词条 |

关掉启动器窗口 = 退出 mod。

### 它怎么做到的

不修改任何游戏文件，运行时读写进程内存：

```
PlantsVsZombies.exe   ← 原版，一个字节都没改
        ↑ 读写内存（Plant+0x5C / Zombie+0x2C / gLawnApp）
PVZ海克斯版.exe
```

关掉程序，游戏立刻恢复原样。代价是**版本必须严格对应**——所有偏移量都是从
1.0.0.1051 中文版逆向出来的。所以启动器会校验 4 处代码段指纹，不符就拒绝启动：

| VA | 字节 | 含义 |
|---|---|---|
| `0x44E969` | `a3 c0 9e 6a 00` | `gLawnApp = new LawnApp` |
| `0x407CA9` | `68 00 70 05 00` | `sizeof(Board) = 0x57000` |
| `0x45F8A9` | `83 46 58 ff` | 植物射击倒计时 `add [esi+0x58],-1` |
| `0x45F8BF` | `8b 4e 5c` | 读射击间隔 `mov ecx,[esi+0x5c]` |

不用 md5 判定，是因为本项目自己就给 exe 打过补丁（自动收阳光，文件偏移
`0x3158F`，`0x75→0xEB`），md5 会把「版本对但打过补丁」的玩家全部误杀。

遇到问题：`PVZ海克斯版.exe --diag`，把输出整段发出来即可定位。
分发细节见 [`docs/分发指南.md`](docs/分发指南.md)。

---

## 仓库里有什么

| 部分 | 状态 | 位置 |
|---|---|---|
| **海克斯版** | ✅ 47 条词条全部实现，176 项测试 | `tools/hextech*.py` |
| 贴图工具链 | ✅ 可用，AI 出图 → 游戏分部件 PNG | `tools/*_textures.py`、`tools/build_mod.py` |
| 行为模块（DLL 注入） | ⏸ 已暂停，三条钩子零命中 | `behavior/` |

### 海克斯版：开发

```bat
run.bat tools\test_hextech.py        :: 176 项离线回归，不需要游戏
run.bat tools\hextech.py run         :: 启动覆盖层（需要游戏在跑）
run.bat tools\hextech.py panel       :: 打开词条面板
run.bat tools\probe_shoot.py test 12 :: 上机验证「真的多射豌豆」
run.bat tools\make_release.py        :: 打包发布版
```

改词条看 `tools/hextech_core.py` 里的 `HEX_DEFS`；数值参数集中在 `Engine` 类的
常量块。设计文档：[`docs/hextech_design.md`](docs/hextech_design.md)，
数值表：[`docs/hextech_values.md`](docs/hextech_values.md)。

已知限制：僵尸的「啃食间隔」字段尚未定位，僵尸攻速类词条用等量增伤模拟；
植物侧已改走真实射击间隔（`Plant+0x5C`），是真的多射豌豆。

---

## 边界

免费同人作品，不售卖、不内购、**不打包游戏本体**。
只在本机内存里改数值，不改 exe、不改 `main.pak`、不动存档。

---

# 附录 A：贴图工具链

一套给「AI 出图 + 原版引擎换皮」用的最小工作流。

> 接手开发的 AI/工程师请先读 [`HANDOFF.md`](HANDOFF.md)：里面有当前状态、已验证内容、已知坑和下一步任务。

它解决的是最烦的那一段：**AI 图怎么变成游戏里能动的贴图，并且不破坏原动画。**

---

## 它为什么能工作

PVZ1 的植物动画是**分部件**引用的。以豌豆射手为例，`reanim/` 里是这些独立 PNG：

```
PeaShooter_Head.png
PeaShooter_mouth.png
PeaShooter_Lips.png
PeaShooter_stalk_bottom.png
PeaShooter_stalk_top.png
PeaShooter_backleaf.png
...
```

`.reanim` 文件只记录每个部件的位置、缩放、旋转关键帧。所以：

> **只要保持 PNG 的画布尺寸不变，替换内容不会破坏动画。**

这套工具做的就是：抠图 → 缩放 → 对齐到原部件的 alpha 包围盒 → 覆盖同名 PNG。
游戏仍然按原来的骨骼动画播放，但看起来已经是你的人物了。

---

## 包含什么

| 路径 | 作用 |
|---|---|
| `tools/scan_textures.py` | 扫描解包后的 `reanim/*.png`，生成尺寸表 |
| `tools/prepare_textures.py` | 抠图、缩放、对齐、覆盖，生成前后对比图 |
| `tools/build_mod.py` | 合并文本、校验贴图、生成 `main_unpack` 和发布包 |
| `content/plants.csv` | 20 个「招笑向」植物企划，含名字/机制/台词/图鉴 |
| `content/parts.csv` | AI 原图 → 游戏部件的映射表 |
| `content/texture_map.csv` | 扫描生成的部件尺寸表 |
| `prompts/style.md` | 固定风格、负面词、换头/换身体 prompt |
| `docs/week1.md` | 第一周逐日任务和验收标准 |
| `HANDOFF.md` | 给下一个 AI/工程师的完整交接文件 |
| `run.bat` | 自动建 venv、装依赖、UTF-8 运行脚本 |

---

## 10 分钟快速开始

### 0. 准备

- Python 3.10+
- [PopStudio](https://github.com/PopGameTool/PopStudio)（解包/打包 `main.pak`）
- 一份中文底包（当前 `config.json` 已按 `1.0.0.1051-zh` 配置；英文版只换图也可，但中文文本会缺字形）
- 当前目录里已经有 `config.json`；换机器时再复制 `config.example.json`

### 1. 解包

用 PopStudio 解包游戏目录的 `main.pak`，把解包内容放到：

```
work/unpacked/
```

确认存在：

```
work/unpacked/reanim/
work/unpacked/properties/LawnStrings.txt
```

### 2. 扫描

```bat
run.bat tools\scan_textures.py --unpacked work\unpacked
```

### 3. 出第一张图

按 `prompts/style.md` 生成一个“傻豆射手”的头，保存为：

```
assets/raw/sha_dou_head.png
```

### 4. 处理贴图

```bat
run.bat tools\prepare_textures.py --config config.json --only peashooter --preview
```

打开 `build/preview.png` 看前后对比。不满意就调 `content/parts.csv` 里的 `scale` / `offset_x` / `offset_y`。

### 5. 构建

把 `content/plants.csv` 里 `peashooter` 的 `status` 改成 `art`，然后：

```bat
run.bat tools\build_mod.py --config config.json
```

### 6. 打包

用 PopStudio 对 `build/main_unpack` 执行 **Pack**。
因为文件夹名以 `_unpack` 结尾，输出会自动是：

```
build/main.pak
```

### 7. 测试

备份游戏目录里的 `main.pak`，把新 `main.pak` 覆盖进去，启动游戏，进 1-1 种豌豆射手。

---

## 目录结构

```
pvz-mod-kit/
├─ config.json                 # 你的路径配置（从 example 复制）
├─ run.bat                     # 推荐入口，自动处理 venv + UTF-8
├─ content/
│  ├─ plants.csv               # 植物企划/文本
│  ├─ parts.csv                # AI 图 → reanim 部件
│  ├─ texture_map.csv          # 扫描生成，别手改
│  └─ strings_extra.csv        # 额外的 LawnStrings 段落
├─ prompts/style.md
├─ docs/week1.md
├─ assets/raw/                 # 放 AI 原图
├─ work/
│  ├─ unpacked/                # 解包后的游戏资源（源）
│  └─ backup/                  # 原始部件备份，脚本自动建
└─ build/
   ├─ main_unpack/             # 合并后的完整资源（打包用）
   ├─ main.pak                 # PopStudio 输出
   ├─ preview.png
   └─ release/                 # main.pak + 说明 + mod_info
```

**只改 `work/unpacked` 和 `content/`，不要手改 `build/main_unpack`。** 每次构建都会覆盖它。

---

## 三个脚本的分工

### `scan_textures.py`

做什么：读 `reanim/*.png`，记录宽高和 alpha 包围盒。

何时跑：解包后一次；换底包后重跑。

### `prepare_textures.py`

做什么：

1. 读 `content/parts.csv`
2. 从 `assets/raw/` 读 AI 图
3. 去掉纯色背景（从四角 flood fill，不会吃掉眼睛里的白色）
4. 裁剪透明边
5. 按原部件的 alpha 包围盒缩放、居中
6. 覆盖 `work/unpacked/reanim/<part_file>`
7. 第一次覆盖前自动备份到 `work/backup/`

常用参数：

| 参数 | 作用 |
|---|---|
| `--only peashooter` | 只处理一个植物 |
| `--preview` | 生成 `build/preview.png` |
| `--outline 1` | 加 1px 描边 |
| `--tolerance 60` | 背景不干净时调大 |
| `--strict` | 有缺失就报错退出 |

### `build_mod.py`

做什么：

1. 复制 `work/unpacked` → `build/main_unpack`
2. 把 `plants.csv` 的名字/图鉴写进 `LawnStrings.txt`（保持原编码）
3. 应用 `strings_extra.csv`
4. 校验贴图是否真的替换了
5. 生成 `build/release/` 和 zip

常用参数：

| 参数 | 作用 |
|---|---|
| `--all` | 忽略 `status`，所有企划都写文本（预览用） |
| `--skip-copy` | 复用已有 `main_unpack`，加快迭代 |

---

## content/*.csv 怎么用

### `plants.csv`

一行一个植物槽位。`status` 决定它是否进入正式文本：

| status | 含义 | 是否写入文本 |
|---|---|---|
| `idea` | 只有点子 | 否（`--all` 时是） |
| `art` | 图已换 | 是 |
| `coded` | 行为也改了 | 是 |
| `done` | 可发布 | 是 |
| `skip` | 暂时不要 | 否 |

`art_focus` 只是备注，告诉你先换哪个部件（Head / Body）。

### `parts.csv`

一行一个部件。`part_file` 必须和 `texture_map.csv` 里的文件名**完全一致**（大小写敏感）。

| 列 | 说明 |
|---|---|
| `part_file` | 游戏里的 PNG 文件名，如 `PeaShooter_Head.png` |
| `raw_file` | `assets/raw/` 里的 AI 图文件名 |
| `scale` | 1.0 = 填满原部件的包围盒；头建议 1.1～1.4 |
| `offset_x/y` | 像素微调，正数向右/向下 |
| `mode` | `center` = 对齐原部件中心；`full` = 填满整个画布 |
| `status` | 只是备注；只要 `raw_file` 存在就会处理 |

### `strings_extra.csv`

想改植物名/图鉴以外的文本（菜单、戴夫台词、商店），在这里加：

```csv
key,value
[YOUR_KEY],你的文本
```

脚本会替换或追加对应的 `[KEY]` 段。

---

## 底包选择（很重要）

| 底包 | 优点 | 缺点 | 适合 |
|---|---|---|---|
| **英文 1.0.0.1051** | 教程、内存地址、CE 脚本最多 | 默认字体不含中文 | 只换图；跟汇编教程 |
| **中文 1.0.0.1051** ★ | 中文正常，且与英文版**代码段一致** | 需要自己找中文底包 | **本项目实际使用** |
| 中文 人人网 1.1.0.1056 | 中文显示正常 | 内存地址和英文教程不同 | 中文名字/图鉴 + 换图 |
| 年度版 1.2.0.1073/1096 | 宽屏、成就 | 部分工具/修改器不兼容 | 不建议第一版 |

> ★ 本项目所有偏移量（`Plant+0x5C`、`Zombie+0x2C`、`gLawnApp`）都来自
> **中文 1.0.0.1051**：`PlantsVsZombies.exe` 3,007,800 字节，ImageBase `0x400000`，
> 4 个节，无壳。海克斯版的启动器会用 4 处代码段指纹校验，版本不符直接拒绝启动。

结论：

- **只换图、不做中文文本** → 英文 1.0.0.1051 最省事。
- **要做中文名字/图鉴** → 用中文底包。字节编码脚本会自动识别（UTF-8 / GBK 都会保持）。
- **要跑海克斯版** → 必须是**中文 1.0.0.1051**，别的版本偏移全错。

> 英文底包写中文，游戏里显示方块，不是脚本 bug，是字体没有中文字形。
> 解决方式只有两个：换中文底包，或使用字体补丁（外挂字体）。

---

## 打包与发布

### 打包

PopStudio 的 Pak → Pack 需要一个以 `_unpack` 结尾的文件夹。
本工具故意把 staging 命名成 `build/main_unpack`，所以：

- 选择 `build/main_unpack`
- Pack
- 输出 `build/main.pak`

如果你改了 `config.json` 里的 `staging_dir`，记得让文件夹名以 `_unpack` 结尾。

### 发布包

再跑一次：

```bat
run.bat tools\build_mod.py --config config.json
```

`build/release/` 会包含：

```
main.pak
README_安装说明.txt
mod_info.json
preview.png
```

以及一个 zip。

**不要打包整个游戏本体。** 只发 `main.pak` + 说明，让玩家自备正版。

### 海克斯版（运行时 mod）的分发

海克斯版不是 pak 补丁，而是挂在游戏旁边的独立程序，分发方式完全不同：

```bat
.venv\Scripts\python.exe tools\make_release.py --version 1.6.7
.venv\Scripts\python.exe tools\gh_release.py --version 1.6.7
```

`make_release.py` 在 `build/release_hextech/` 产出本地中文名 zip；
`gh_release.py` 负责建 Release 并上传，**上传时会映射成 ASCII 资产名**
（GitHub 的 API 会把 `name` 参数里的非 ASCII 字符整段剥掉，
`PVZ海克斯版_v1.6.7.zip` 会变成 `PVZ._v1.6.7.zip`，所以必须换名）。

Releases 上的资产：

| 文件 | 大小 | 说明 |
|---|---|---|
| `PVZ_Hextech_v1.6.7.zip` | ~29 MB | 单文件 exe，玩家双击即用，不需要 Python |
| `PVZ_Hextech_v1.6.7_src.zip` | ~70 KB | 纯 Python 脚本，杀软误报时的兜底方案 |

玩家侧流程：

```
双击 exe → 启动器自动找游戏 → 校验 4 处代码段指纹
        → 通过则拉起覆盖层；不通过则明确拒绝并说明原因
```

关键设计：

- **版本校验不用 md5**。本项目自己就给 exe 打过「自动收阳光」单字节补丁，
  用 md5 会把「版本对但打过补丁」的玩家全误杀。改用 4 处代码段指纹
  （`tools/version_check.py`），普通补丁不会动这些地方。
- **不通过就不启动**。偏移量绑死 1.0.0.1051 中文版，版本不对强行运行会改错内存。
- **两个包都发**。exe 版零门槛但容易被杀软误判（读别人进程内存的特征），
  源码版让玩家能自己看每一行代码。
- `--noupx` 打包，不用压缩壳，显著降低误报率。

诊断：`PVZ海克斯版.exe --diag`，输出 Python 版本 / 权限 / 进程 / 路径 / 校验结果，
玩家反馈问题时让他整段复制。

详细说明见 [`docs/分发指南.md`](docs/分发指南.md)。

---

## 测试清单

发布前至少过一遍：

- [ ] 主菜单正常
- [ ] 1-1 能进，选卡界面名字正确
- [ ] 换皮植物会正常晃动/攻击
- [ ] 图鉴能打开，描述没有乱码
- [ ] 植物受伤/死亡时没有露出原版贴图（变体部件要一起换）
- [ ] 1-5 能过
- [ ] 无尽模式能撑 10 波
- [ ] 退出重进不崩
- [ ] 备份的 `main.pak` 能正常还原

---

## 常见问题

**Q：贴图没变化？**
A：按顺序查：`build/preview.png` 对不对 → `build/main_unpack/reanim/` 里的文件对不对 → 有没有重新 Pack → 游戏目录是不是你测试的那份。

**Q：游戏里是粉色方块？**
A：文件名/尺寸/格式不匹配。不要手动改 PNG 尺寸，交给 `prepare_textures.py`。

**Q：头太大/太小/偏移？**
A：调 `parts.csv` 的 `scale` / `offset_x` / `offset_y`，重新跑 `prepare_textures.py`。脚本每次都用 `work/backup` 里的原图做几何参考，反复跑不会漂移。

**Q：换头后，攻击时又变回原版？**
A：那个植物有多个头部变体（如 `SunFlower_head_sing1..5`、`Wallnut_cracked1/2`）。在 `texture_map.csv` 里搜前缀，把所有变体都加到 `parts.csv`。

**Q：背景抠不干净？**
A：让 AI 重新出纯洋红 `#FF00FF` 底；或把 `--tolerance` 调到 `60`～`80`；复杂背景建议用 `rembg`。

**Q：中文是方块？**
A：底包字体问题，换中文底包或字体补丁。见上面“底包选择”。

**Q：`ModuleNotFoundError`？**
A：用 `run.bat`，不要直接 `python tools/xxx.py`。`run.bat` 会自动建 `.venv` 并装依赖。

**Q：PopStudio 打包失败？**
A：确认选的是 `build/main_unpack` 这一层，不是里面的 `reanim`；确认没有游戏进程占用文件。

---

## 版权与发布

- 这是免费同人改版，**不要售卖、不要内购、不要打包原版游戏本体**。
- 不要用真人脸/真人声音做素材，除非本人书面同意。肖像权、声音权风险很高。
- 二创 IP（动漫/游戏角色）免费 fan mod 通常被容忍，但不要商业化，也别碰维权强的公司。
- B站/抖音发布 AI 生成内容时，按平台要求勾选“AI 生成”标识。
- 保留 prompt、原图、生成记录，方便自证素材来源。

---

## 下一步

第一周跑通后，按这个顺序加：

1. **更多头/身体**：把 `plants.csv` 里的 20 个企划逐个做成 `art`
2. **变体部件**：受伤、攻击、眨眼状态一起换
3. **音效/台词**：TTS 生成，替换 `sounds/` 下的 `.ogg`
4. **行为魔改**：Cheat Engine 找地址 → 汇编补丁（参考 [改版教程系列](https://www.bilibili.com/video/BV1TK421k7ar)）
5. **动画**：`.fla` → `.compiled`，做真正的新动作
6. **完整改版**：关卡、UI、启动器、安装包

第一周只需要做到第 1 步的一半。**先发一个 15 秒视频，比做完 49 个植物更重要。**
