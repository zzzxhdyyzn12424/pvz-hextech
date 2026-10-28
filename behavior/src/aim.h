// ============================================================================
//  aim.h / aim.cpp  ——  落点计算
//
//  用户选定的空枪策略：
//    "查本行最近的僵尸，让子弹掉在它前面 LAND_AHEAD_PX 处"
//
//  也就是说空枪不是"随机乱飞"，而是"瞄了、但砸在僵尸脚边"——
//  这样看起来才像一个真的打偏了的射手，而不是随机数。
//
//  边界情况都要有确定行为，不能返回垃圾值：
//    - 本行没有僵尸          -> 掉在默认距离 DEFAULT_LAND_PX
//    - 僵尸在植物左边（身后）-> 同样用默认距离，否则会往左飞
//    - 僵尸太近（< 20px）    -> 用最小距离 MIN_LAND_PX
//    - 僵尸太远（> 屏幕）    -> 钳到屏幕内
// ============================================================================

#pragma once
#include "pvz_types.h"

namespace Aim {

// 落点参数
constexpr int   LAND_AHEAD_PX   = 25;    // 僵尸前方多少 px 落地
constexpr int   DEFAULT_LAND_PX = 110;   // 本行没僵尸时的飞行距离
constexpr int   MIN_LAND_PX     = 45;    // 最近也要飞这么远，别糊自己脸上
constexpr int   MAX_LAND_PX     = 850;   // 最远，防止飞出屏幕
constexpr float ARC_PEAK_PX     = 34.0f; // 弧顶抬高多少
constexpr int   ZOMBIE_HIT_W    = 28;    // 僵尸身体的碰撞半宽

// 在 board 里找 row 行上、位于 fromX 右边最近的活僵尸。
// 找不到返回 nullptr。
PVZ::Zombie* FindNearestZombieAhead(PVZ::Board* board, int row, int fromX);

// 计算一棵植物的"空枪"落点 x 与地面 y。
//   plant   —— 射手
//   board   —— 当前场地（可为 nullptr，那就走默认距离）
//   outLandX / outGroundY —— 输出
// 返回 true 表示找到了目标僵尸，false 表示用的默认距离。
bool ComputeMissLanding(PVZ::Plant* plant, PVZ::Board* board,
                        int* outLandX, int* outGroundY);

} // namespace Aim
