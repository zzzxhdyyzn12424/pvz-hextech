// ============================================================================
//  pvz_types.h  ——  PVZ 1.0.0.1051-zh 关键结构体布局
//
//  ⚠ 所有偏移量都是**待真机核对**的。核对方法见 aob_patterns.h。
//    结构体只声明到需要的字段为止，中间用 padding 占位，
//    sizeof 和字段偏移都靠 padding 精确对齐 —— 不要随手改 padding 长度。
// ============================================================================

#pragma once
#include <cstdint>
#include <windows.h>

namespace PVZ {

// ---------------------------------------------------------------------------
// 常量
// ---------------------------------------------------------------------------
constexpr int ROW_COUNT   = 5;      // 草坪 5 行
constexpr int COL_COUNT   = 9;      // 9 列
constexpr int CELL_W      = 80;     // 一格宽 px
constexpr int CELL_H      = 100;    // 一格高 px
constexpr int BOARD_LEFT  = 40;     // 草坪左边缘（相对窗口）
constexpr int BOARD_TOP   = 80;     // 草坪上边缘

// ---------------------------------------------------------------------------
// 植物类型（mPlantType）
// ---------------------------------------------------------------------------
enum PlantType : int {
    PT_NONE            = -1,
    PT_PEASHOOTER      = 0,
    PT_SUNFLOWER       = 1,
    PT_CHERRYBOMB      = 2,
    PT_WALLNUT         = 3,
    PT_POTATOMINE      = 4,
    PT_SNOWPEA         = 5,
    PT_CHOMPER         = 6,
    PT_REPEATER        = 7,
    PT_PUFFSHROOM      = 8,
    PT_SUNSHROOM       = 9,
    PT_FUMESHROOM      = 10,
    PT_GRAVEBUSTER     = 12,
    PT_HYPNOSHROOM     = 13,
    PT_SCRAREDYSHROOM  = 14,
    PT_ICESHROOM       = 15,
    PT_DOOMSHROOM      = 16,
    PT_LILYPAD          = 17,
    PT_SQUASH           = 18,
    PT_THREEPEATER      = 19,
    PT_TANGLEKELP       = 20,
    PT_JALAPENO         = 21,
    PT_SPIKEWEED        = 22,
    PT_TORCHWOOD        = 23,
    PT_TALLNUT          = 24,
    PT_SEASHROOM        = 25,
    PT_PLANTERN         = 26,
    PT_CACTUS           = 27,
    PT_BLOVER           = 28,
    PT_SPLITPEA         = 29,
    PT_STARFRUIT        = 30,
    PT_PUMPKIN          = 31,
    PT_MAGNETSHROOM     = 32,
    PT_CABBAGEPULT      = 33,
    PT_FLOWERPOT        = 34,
    PT_KERNELPULT       = 35,
    PT_COFFEEBEAN       = 36,
    PT_GARLIC           = 37,
    PT_UMBRELLALEAF     = 38,
    PT_MARIGOLD         = 39,
    PT_MELONPULT        = 40,
    PT_GATLINGPEA       = 41,
    PT_TWINSUNFLOWER    = 42,
    PT_GLOOMSHROOM      = 43,
    PT_CATTAIL          = 44,
    PT_WINTERMELON      = 45,
    PT_GOLD_MAGNET      = 46,
    PT_SPIKEROCK        = 47,
    PT_COBCANNON        = 48,
    PT_IMITATER         = 49,
};

// ---------------------------------------------------------------------------
// 子弹类型（Pea::mPeaType）
// ---------------------------------------------------------------------------
enum PeaType : int {
    PEA_NORMAL         = 0,
    PEA_SNOW           = 1,
    PEA_FIRE           = 2,
    PEA_RANDOM         = 3,   // 随机偏航（我们借用它的视觉，但自己控制轨迹）
    PEA_CACTUS         = 4,
    PEA_STAR           = 8,
};

// ---------------------------------------------------------------------------
// Pea —— 豌豆子弹
// ---------------------------------------------------------------------------
#pragma pack(push, 4)
struct Pea {
    // ⚠ 这个布局是**带证据的推断**，还不是实测确认：
    //   - 0x00520AC0（每帧每颗豌豆调一次的刷新函数）里
    //       mov ecx, [esi+0x34] / test ecx,ecx / movzx eax,cx
    //       cmp eax,[edx+8] / shl eax,5 / add eax,[edx]
    //       cmp [eax+0x9C], ecx
    //     证明 +0x34 是"格子索引"（int，0..44），用来查 board 网格。
    //   - 同一函数里 fld [esi+0x24] / fld [esi+0x28] 经 __ftol 转 int
    //     后当坐标传给绘制例程，说明 +0x24/+0x28 是 float 坐标。
    //   - 因此 +0x34 若是速度就和"格子索引"冲突 —— 结论是
    //     **0x00520AC0 的 esi 与 AddPea 里写 +0x34 的对象不是同一个**。
    //   真正确认要靠 tangshi.log 里的探针 dump（见 ProbeDump）。
    uint8_t  pad_00[0x24];
    float    mX;                // 0x24  世界坐标 x（float）—— 待探针确认
    float    mY;                // 0x28  世界坐标 y（float）—— 待探针确认
    uint8_t  pad_2C[0x08];
    int      mCell;             // 0x34  所在格索引 0..44（int，已从反汇编确认）
    uint8_t  pad_38[0x04];
    int      mRow;              // 0x3C
    uint8_t  pad_40[0x08];
    int      mVelX;             // 0x48  ← 占位，探针确认后改
    int      mVelY;             // 0x4C
    uint8_t  pad_50[0x0C];
    int      mDamage;           // 0x5C
    bool     mDead;             // 0x60
    uint8_t  pad_61[0x03];
    int      mPeaType;          // 0x64
    uint8_t  pad_68[0x10];
    Pea*     mNext;             // 0x78  子弹链表（待确认）
};
#pragma pack(pop)

// ---------------------------------------------------------------------------
// 前向声明
// ---------------------------------------------------------------------------
struct App;
struct Board;

// ---------------------------------------------------------------------------
// Plant
// ---------------------------------------------------------------------------
#pragma pack(push, 4)
struct Plant {
    uint8_t    pad_00[0x08];
    int        mX;              // 0x08  格左上角世界坐标
    int        mY;              // 0x0C
    uint8_t    pad_10[0x0C];
    int        mRow;            // 0x1C
    int        mCol;            // 0x20
    uint8_t    pad_24[0x04];
    int        mPlantType;      // 0x28
    uint8_t    pad_2C[0x08];
    int        mHealth;         // 0x34
    int        mMaxHealth;      // 0x38
    uint8_t    pad_3C[0x14];
    int        mShootCooldown;  // 0x50  剩余射击冷却帧数
    int        mShootInterval;  // 0x54  射击间隔帧数
    uint8_t    pad_58[0x0C];
    bool       mDead;           // 0x64
    uint8_t    pad_65[0x0B];
    App*       mApp;            // 0x70
    uint8_t    pad_74[0x08];
    Board*     mBoard;          // 0x7C
};
#pragma pack(pop)

// ---------------------------------------------------------------------------
// Zombie
// ---------------------------------------------------------------------------
#pragma pack(push, 4)
struct Zombie {
    uint8_t    pad_00[0x08];
    int        mX;              // 0x08
    int        mY;              // 0x0C
    uint8_t    pad_10[0x0C];
    int        mRow;            // 0x1C
    uint8_t    pad_20[0x04];
    int        mZombieType;     // 0x24
    uint8_t    pad_28[0x08];
    bool       mDead;           // 0x30
    bool       mInVase;         // 0x31
    uint8_t    pad_32[0x02];
    int        mBodyHealth;     // 0x34
    int        mHelmHealth;     // 0x38
    int        mShieldHealth;   // 0x3C
    int        mFacing;         // 0x40  待实测确认符号语义
    int        mSpeed;          // 0x44
    uint8_t    pad_48[0x1C];
    bool       mHasHead;        // 0x64
    uint8_t    pad_65[0x0F];
    Zombie*    mNext;           // 0x74
};
#pragma pack(pop)

// ---------------------------------------------------------------------------
// Board
// ---------------------------------------------------------------------------
#pragma pack(push, 4)
struct Board {
    uint8_t    pad_00[0x08];
    App*       mApp;            // 0x08
    uint8_t    pad_0C[0x0C];
    Plant**    mPlantGrid;      // 0x18  [5][9] 摊平
    uint8_t    pad_1C[0x04];
    Zombie*    mZombieList;     // 0x20  僵尸链表头
    Pea*       mPeaList;        // 0x24  子弹链表头 ← 待核对
    uint8_t    pad_28[0x3C];
    int        mLevel;          // 0x64
    int        mWave;           // 0x68
};
#pragma pack(pop)

// ---------------------------------------------------------------------------
// App —— 全局单例
// ---------------------------------------------------------------------------
#pragma pack(push, 4)
struct App {
    uint8_t    pad_00[0x08];
    Board*     mBoard;          // 0x08
    uint8_t    pad_0C[0x0C];
    uint32_t   mUpdateCount;    // 0x18  帧计数，暂停时不增长
    uint8_t    pad_1C[0x2C];
    int        mGameMode;       // 0x48
    uint8_t    pad_4C[0x08];
    bool       mPaused;         // 0x54
};
#pragma pack(pop)

// ---------------------------------------------------------------------------
// 内联小工具
// ---------------------------------------------------------------------------
inline PlantType GetPlantType(const Plant* p) {
    return static_cast<PlantType>(p->mPlantType);
}

// 植物中心点（植物 mX/mY 是格左上角）
inline int PlantCenterX(const Plant* p) { return p->mX + CELL_W / 2; }
inline int PlantCenterY(const Plant* p) { return p->mY + CELL_H / 2; }

// 僵尸是否可以被命中
inline bool ZombieTargetable(const Zombie* z) {
    return z && !z->mDead && !z->mInVase && z->mHasHead;
}

} // namespace PVZ
