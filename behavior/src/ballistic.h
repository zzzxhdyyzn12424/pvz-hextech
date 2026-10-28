// ============================================================================
//  ballistic.h / ballistic.cpp  ——  抛物线子弹系统
//
//  设计目标：
//    原版豌豆是匀速直线（mVelY 恒为 0）。要让"空枪"看起来像抛物线，
//    必须在 Pea::Update 里自己积分 vy —— 每帧 vy += g，y += vy。
//
//  关键难点：
//    1. 原版 Pea::Update 会自己写 mY 吗？不知道。如果它每帧把 mY 重置成
//       固定值，我们在它之后改 mY 才有效（后置钩子，顺序很重要）。
//    2. 豌豆什么时候算"落地"？我们自己定：y >= 目标地面 y 时置 mDead。
//    3. 一颗豌豆只能被接管一次 —— 用旁挂表记"我已经处理过这个指针"。
//
//  落地判定策略（用户选的）：
//    查本行最近的僵尸，让子弹掉在它前方 LAND_AHEAD_PX 处。
//    本行没有僵尸 -> 掉在默认距离。
// ============================================================================

#pragma once
#include <cstdint>
#include "pvz_types.h"

namespace Ballistic {

// 一颗被接管的豌豆轨迹
struct Track {
    float   groundY   = 0.0f;   // 落地时的 y
    float   gravity   = 0.0f;   // px / frame^2
    float   vy0       = 0.0f;   // 初始垂直速度（负 = 向上抛）
    int     spawnX    = 0;      // 出生 x
    int     landX     = 0;      // 预期落点 x
    float   startY    = 0.0f;   // 出生 y
    bool    landed    = false;
    uint32_t age      = 0;      // 存活帧数（防泄漏）
};

// 初始化（读配置、分配表）
void Init();

// 让某颗豌豆变成抛物线。调用方负责算好 landX / groundY。
//   pea      —— 豌豆指针
//   landX    —— 期望落点 x
//   groundY  —— 落地时的 y
//   arcPeak  —— 弧线最高抬高多少 px（相对出生点）
void MakeArcing(PVZ::Pea* pea, int landX, int groundY, float arcPeak);

// 每帧推进所有被接管的豌豆。返回本轮"落地"的豌豆数量。
//   selfDestruct —— 落地后是否由我们自己置 mDead
int  UpdateAll(bool selfDestruct);

// 把某颗豌豆从表里移除（它被击中/出屏了）
void Forget(PVZ::Pea* pea);

// 当前被接管的数量（调试用）
int  Count();

} // namespace Ballistic
