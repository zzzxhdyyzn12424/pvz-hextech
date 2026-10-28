# AI 出图提示词（原版换皮专用）

## 一条铁律

**先只换头。** 不要一上来生成整株植物。

原因：PVZ 的植物是分部件动画（头、嘴、茎、叶、眼睛都是独立 PNG）。
只换 `*_Head.png`，身体和动画全部保留，效果立刻出来，而且几乎不会错位。
整株替换是第二步，等管线跑顺了再做。

---

## 1. 风格锁（每条 prompt 都放最前面）

```
Plants vs. Zombies 1 mod sprite, 2D hand-drawn cartoon, thick dark outline,
flat cel shading, saturated colors, clean silhouette, centered,
solid magenta background (#FF00FF), no shadow, no text, no UI, 1024x1024
```

> 用**洋红**，不要用绿幕。植物本身就是绿的，绿幕抠图会吃掉叶子。
> 也不要用“透明背景”，多数模型会给你假透明或白底。

## 2. 负面词（固定带上）

```
realistic, 3D render, photorealistic, blurry, extra limbs, extra fingers,
deformed hands, text, watermark, logo, background scenery, multiple characters,
cropped, out of frame, gradient background, drop shadow
```

## 3. 换头 prompt 模板

```
{STYLE_BLOCK}

Just the HEAD of {角色描述}, no stem, no leaves, no body, no pot.
{表情/特征描述}.
The head fills 75% of the frame, front view, slightly 3/4, readable at 64px.
{NEGATIVE}
```

### 示例：傻豆射手

```
Plants vs. Zombies 1 mod sprite, 2D hand-drawn cartoon, thick dark outline,
flat cel shading, saturated colors, clean silhouette, centered,
solid magenta background (#FF00FF), no shadow, no text, no UI, 1024x1024

Just the HEAD of a derpy pea plant, no stem, no leaves, no body.
Cross-eyed, buck teeth, one eyebrow higher than the other, deeply confident
but clearly wrong expression. The head fills 75% of the frame, front view,
readable at 64px.

realistic, 3D render, photorealistic, blurry, extra limbs, extra fingers,
deformed hands, text, watermark, logo, background scenery, multiple characters,
cropped, out of frame, gradient background, drop shadow
```

## 4. 换嘴/表情 prompt

嘴是独立部件时，只生成嘴，不要带头：

```
{STYLE_BLOCK}

Just a cartoon mouth, {表情}, no face, no head, no body.
Centered, readable at 32px.
{NEGATIVE}
```

表情建议只做 2～3 个：待机、攻击、被打。不要做 10 个，游戏里根本看不清。

## 5. 换整株/身体 prompt

```
{STYLE_BLOCK}

Full body {角色描述}, standing, feet at the bottom center, front view.
The character fills 80% of the frame, simple readable silhouette.
{NEGATIVE}
```

生成后检查：
- 脚/底座在画面底部，别悬空
- 左右对称或明确朝向（PVZ 植物默认朝右）
- 没有多余的手/脚/文字

## 6. 一致性的 5 个办法

1. **同一段 style block 不改**，每条 prompt 原样复制。
2. 第一张满意的图存成 `assets/raw/_ref_<角色>.png`，后续所有姿势都用它做 reference（图生图 / character reference）。
3. 同一模型、同一会话、同一 seed（SD）里连续出。
4. 先出**正面三视图**（正面/侧面/背面），确认形象稳定，再出表情。
5. 一次出 4 张，只挑 1 张。不要在一张图上反复抽，越抽越偏。

## 7. 抽象度调节

| 程度 | 加这些词 | 效果 |
|---|---|---|
| 微抽象 | `slightly goofy, derpy` | 可爱但不对劲 |
| 招笑 | `absurd, exaggerated expression, ugly-cute, meme energy` | 一眼想笑 |
| 猎奇 | `grotesque, unsettling, cursed, but still cute` | 适合恐怖/整活向 |

不要直接写“像招笑版/神人版”，模型不认识；写具体的视觉特征。

## 8. 出图后处理

1. 检查手指/牙齿/文字，崩了就用局部重绘，不要整张重抽。
2. 缩到游戏实际大小（64～128px）看一眼，认不出就重做剪影。
3. 背景不干净就重出一张，不要指望脚本抠掉复杂背景。
4. 需要描边时，`prepare_textures.py --outline 1` 或 `--outline 2`。
