// ============================================================================
//  aim.cpp  ——  落点计算实现
// ============================================================================

#include "aim.h"
#include <algorithm>

namespace Aim {

namespace {

// 链表节点数上限 —— 防止游戏内部链表坏掉时把我们拖死
constexpr int MAX_WALK = 512;

} // namespace

PVZ::Zombie* FindNearestZombieAhead(PVZ::Board* board, int row, int fromX) {
    if (!board) return nullptr;

    PVZ::Zombie* best = nullptr;
    int bestX = 0x7FFFFFFF;

    PVZ::Zombie* z = board->mZombieList;
    int guard = 0;

    while (z && guard++ < MAX_WALK) {
        if (PVZ::ZombieTargetable(z) && z->mRow == row && z->mX > fromX) {
            if (z->mX < bestX) {
                bestX = z->mX;
                best  = z;
            }
        }
        z = z->mNext;
    }

    return best;
}

bool ComputeMissLanding(PVZ::Plant* plant, PVZ::Board* board,
                        int* outLandX, int* outGroundY) {
    if (!plant) return false;

    int fromX   = PVZ::PlantCenterX(plant);
    int row     = plant->mRow;

    // 地面 y：用植物所在格的中心偏下一点。
    // 这种"不查真实地面高度"的做法在草坪上是够的 —— 草坪行高固定 100px。
    int groundY = plant->mY + PVZ::CELL_H - 12;

    PVZ::Zombie* z = FindNearestZombieAhead(board, row, fromX);

    int landX;
    bool found = false;

    if (z) {
        // 掉在僵尸前方 —— 注意僵尸是从右往左走，
        // 所以"前方"对我们来说是 x 更大的一侧（僵尸来的方向）。
        landX = z->mX - LAND_AHEAD_PX;
        found = true;

        // 别落到植物身后
        if (landX - fromX < MIN_LAND_PX) {
            landX = fromX + MIN_LAND_PX;
        }
    } else {
        landX = fromX + DEFAULT_LAND_PX;
    }

    // 上限
    if (landX - fromX > MAX_LAND_PX) {
        landX = fromX + MAX_LAND_PX;
    }

    if (outLandX)   *outLandX   = landX;
    if (outGroundY) *outGroundY = groundY;

    return found;
}

} // namespace Aim
