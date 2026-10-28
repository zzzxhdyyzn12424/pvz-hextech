# 第一周：从 0 到「一个能发视频的 Demo」

目标不是做 49 个植物，而是**跑通一条能重复的流水线**，并做出 3～5 个一眼能认出的换皮植物。

每天 1～2 小时即可。周末你应该有一个 15 秒的短视频素材。

---

## Day 0：准备（30 分钟）

- [ ] 安装 **Python 3.10+**，安装时勾选 “Add Python to PATH”
- [ ] 安装 **PopStudio**（解包/打包 `main.pak`）
- [ ] 准备一份 **PVZ 1.0.0.1051 英文原版**，或中文底包（见 README 的“底包选择”）
- [ ] 复制整个游戏目录一份，专门用来测试，别动你正在玩的存档
- [ ] 把 `config.example.json` 复制为 `config.json`

> 中文文本请优先用中文底包。英文底包默认字体不含中文字形，游戏里会显示方块，这不是脚本的问题。

---

## Day 1：跑通「解包 → 打包 → 进游戏」

### 1. 解包

用 PopStudio 打开游戏目录里的 `main.pak`，选择 Unpack。
它会生成一个 `main_unpack` 文件夹。

把里面的内容放到：

```
pvz-mod-kit/work/unpacked/
```

确认目录长这样：

```
work/unpacked/
  reanim/
  properties/
  images/
  sounds/
  ...
```

### 2. 扫描贴图

```bat
run.bat tools\scan_textures.py --unpacked work\unpacked
```

打开 `content/texture_map.csv`，你应该能看到 `PeaShooter_Head.png`、`SunFlower_head.png`、`Wallnut_body.png` 等。

### 3. 先打一个「原样包」

```bat
run.bat tools\build_mod.py --config config.json
```

这会把 `work/unpacked` 复制到 `build/main_unpack`，并生成文本。此时还没换图，所有植物都是 `idea`，所以不会改任何名字。

用 PopStudio 对 `build/main_unpack` 执行 **Pack**，得到 `build/main.pak`。

把它复制到测试游戏目录（先备份原 `main.pak`），启动游戏。

- [ ] 能进主菜单
- [ ] 能进 1-1
- [ ] 没有闪退

**这一步跑通，说明解包/打包链路没问题。后面所有问题都只可能是贴图或文本。**

---

## Day 2：出 10 个「换头」点子，选 3 个

让 LLM 按这个格式生成 10 个：

> 原植物 + 新名字（谐音/错位）+ 一个极端机制 + 一句台词 + 一句图鉴

筛选标准：

1. 只看名字就能想象画面
2. 机制一句话能说清
3. 换头就能表现，不需要新动画
4. 不依赖真人肖像/敏感梗

把选中的 3 个写进 `content/plants.csv`（可以先保留 `status=idea`）。

然后按 `prompts/style.md` 出图。每个角色出 4 张，挑 1 张。

保存到：

```
assets/raw/sha_dou_head.png
assets/raw/zhao_yang_head.png
assets/raw/jian_qiang_body.png
```

文件名要和 `content/parts.csv` 里的 `raw_file` 一致。

---

## Day 3：把第一个头换进游戏

```bat
run.bat tools\prepare_textures.py --config config.json --only peashooter --preview
```

打开 `build/preview.png`，对比 BEFORE / AFTER。

- 头太小 → 把 `parts.csv` 里对应行的 `scale` 从 `1.15` 调到 `1.3` / `1.5`
- 头偏左/偏右 → 调 `offset_x`
- 头偏上/偏下 → 调 `offset_y`
- 背景没抠干净 → 重新生成纯洋红底；或把 `--tolerance` 调到 `60`～`80`

然后构建。先把 `content/plants.csv` 里 `peashooter` 这一行的 `status` 改成 `art`，这样它才会进入正式文本：

```bat
run.bat tools\build_mod.py --config config.json
```

用 PopStudio 打包 `build/main_unpack` → `build/main.pak`，复制进测试游戏。

进 1-1，种一个豌豆射手。

- [ ] 头变了
- [ ] 还会正常晃动/射击
- [ ] 没有粉色缺图方块
- [ ] 没有闪退

**恭喜，你现在有一个能动的 AI 换皮植物了。**

---

## Day 4：再加 2～3 个

优先选**单部件或部件少**的植物：

| 植物 | 推荐替换 | 难度 |
|---|---|---|
| 坚果墙 | `Wallnut_body.png` + 两个 cracked | 低 |
| 土豆雷 | `PotatoMine_body.png` | 低 |
| 向日葵 | `SunFlower_head.png` | 中（花瓣多，但只换头也能看） |
| 樱桃炸弹 | 多个 body 部件 | 中 |
| 豌豆射手 | `PeaShooter_Head.png` + `mouth` | 低 |

每加一个：

1. 在 `content/parts.csv` 加行（`part_file` 必须和 `texture_map.csv` 完全一致，大小写敏感）
2. 把 AI 图放进 `assets/raw/`
3. 在 `content/plants.csv` 把对应植物的 `status` 改成 `art`
4. `run.bat tools\prepare_textures.py --config config.json`
5. `run.bat tools\build_mod.py --config config.json`
6. 打包，进游戏看

---

## Day 5：名字、图鉴、台词

把 `content/plants.csv` 里已经做完的植物 `status` 改成 `art` 或 `done`。

```bat
run.bat tools\build_mod.py --config config.json
```

这次只会把 `art/coded/done` 的植物写进 `LawnStrings.txt`。

检查：

- [ ] 选卡界面名字变了
- [ ] 图鉴名字和描述变了
- [ ] 中文没有显示成方块（如果有，换中文底包或字体补丁）

> 想先预览全部 20 个企划的文本，可以加 `--all`。正式发布时不要加，避免没做完的植物也改名。

---

## Day 6：15 秒视频素材

录一段：

1. 原版豌豆射手出场（1 秒）
2. 换成傻豆射手（1 秒）
3. 它疯狂空枪，僵尸走到面前（5 秒）
4. 切到图鉴，念一句台词（2 秒）
5. 标题 + “评论区点菜下一个植物”（3 秒）

不需要预告片级剪辑。竖屏、有字幕、前 3 秒有反差，就够了。

---

## Day 7：发布与收集反馈

标题公式：

> 我把豌豆射手做成了傻豆射手，它 40% 的子弹不知道飞哪去了【PVZ 沙雕改版 #1】

简介里写：

- 免费同人，不售卖
- 基于哪个底包
- 当前包含几个植物
- 下期想做谁，评论区点菜
- AI 生成内容说明

---

## 本周验收标准

- [ ] `work/unpacked` 解包成功
- [ ] `build/main_unpack` 能打包成可启动的 `main.pak`
- [ ] 至少 3 个植物完成换头，动画正常
- [ ] `LawnStrings` 名字/图鉴生效
- [ ] 1-1 到 1-5 不闪退
- [ ] 有一段 15 秒可发布视频
- [ ] 所有 prompt、原图、`parts.csv` 已提交 Git

---

## 卡住了看这里

| 现象 | 最可能的原因 | 处理 |
|---|---|---|
| `ModuleNotFoundError: PIL` | 没走 `run.bat` | 用 `run.bat`，它会自动建 venv |
| 找不到 `LawnStrings.txt` | 解包目录不对 | 确认 `work/unpacked/properties/LawnStrings.txt` 存在 |
| 贴图没变化 | 文件名大小写不对，或没重新打包 | 对比 `texture_map.csv`；确认 `build/main_unpack/reanim/` 里的文件已变 |
| 游戏里是粉色方块 | 贴图尺寸/格式不对，或文件名不匹配 | 用脚本处理，不要手动改尺寸；确认 PNG 带 alpha |
| 头太大/太小 | `scale` 不合适 | 调 `parts.csv` 的 `scale`，重新跑 |
| 头偏移 | `offset_x/y` 不合适 | 微调 5～10 像素 |
| 背景有白边/洋红边 | 抠图不干净 | 重新生成纯色底；`--tolerance 60`；`--outline 1` |
| 中文是方块 | 英文底包字体不含中文 | 换中文底包，或只改图不改字 |
| 打包后闪退 | 底包版本不匹配 / pak 损坏 | 换回备份 `main.pak`；确认用的是同一个底包解包再打包 |
