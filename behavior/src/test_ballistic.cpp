// ============================================================================
//  test_ballistic.cpp  ——  抛物线数学的离线自测
//
//  目的：把"落点计算 + 弧线积分"从游戏内存里剥出来单独跑，
//        确认物理本身是对的，再去怀疑偏移量。
//
//  编译（本机没有 gtest，用手写的断言）：
//    i686-w64-mingw32-g++.exe -m32 -std=c++17 -O2 -I src \
//        src\test_ballistic.cpp src\ballistic.cpp src\aim.cpp \
//        -o test_ballistic.exe -static
//
//  这套测试的价值：注入后如果豆子飞得不正常，先跑它。
//  它绿 = 数学没错，问题在 AOB 特征码或结构体偏移。
//  它红 = 别去碰游戏，先把这里修好。
// ============================================================================

#include <cstdio>
#include <cmath>
#include <cstring>
#include <vector>

#include "pvz_types.h"
#include "ballistic.h"
#include "aim.h"

// ---------------------------------------------------------------------------
// 迷你断言框架
// ---------------------------------------------------------------------------
static int g_pass = 0;
static int g_fail = 0;

static void Check(bool cond, const char* what) {
    if (cond) { ++g_pass; printf("  [ok]   %s\n", what); }
    else      { ++g_fail; printf("  [FAIL] %s\n", what); }
}

static bool Near(float a, float b, float eps) {
    return fabsf(a - b) <= eps;
}

// ---------------------------------------------------------------------------
// 造一个假的豌豆，只填我们关心的字段
// ---------------------------------------------------------------------------
static PVZ::Pea* MakePea(int x, int y, float vx) {
    PVZ::Pea* p = new PVZ::Pea();
    memset(p, 0, sizeof(PVZ::Pea));
    p->mX = x;
    p->mY = y;
    p->mVelX = vx;
    p->mVelY = 0.0f;
    p->mDead = false;
    return p;
}

// 模拟逐帧推进，返回落地时的帧数。-1 表示永远没落地（说明参数炸了）
static int Simulate(PVZ::Pea* pea, PVZ::Board* board, int maxFrames = 600) {
    for (int f = 0; f < maxFrames; ++f) {
        // 横向推进（原版是游戏做的，我们这里代劳）
        pea->mX += static_cast<int>(pea->mVelX);
        int landed = Ballistic::UpdateAll(/*selfDestruct=*/true);
        if (landed > 0 || pea->mDead) return f;
        if (pea->mY > 2000 || pea->mY < -2000) return -1;
    }
    return -1;
}

// ---------------------------------------------------------------------------
// 测试 1：基本弧线形状
// ---------------------------------------------------------------------------
static void TestArcShape() {
    printf("\n[1] 弧线形状\n");

    Ballistic::Init();
    PVZ::Pea* pea = MakePea(100, 300, 9.0f);

    int landX = 300, groundY = 380;
    Ballistic::MakeArcing(pea, landX, groundY, 34.0f);

    Check(pea->mVelY < 0.0f, "初始 vy 是负的（先往上抛）");

    int startY = pea->mY;
    int minY = startY;
    std::vector<int> ys;
    ys.push_back(startY);

    for (int f = 0; f < 200; ++f) {
        pea->mX += static_cast<int>(pea->mVelX);
        Ballistic::UpdateAll(true);
        ys.push_back(pea->mY);
        if (pea->mY < minY) minY = pea->mY;
        if (pea->mDead) break;
    }

    Check(pea->mDead, "最终落地（mDead）");
    Check(minY < startY, "轨迹先上升");
    Check(pea->mY >= groundY - 2, "最终 y 到达地面附近");

    // 弧顶抬高应接近 34px
    float peak = static_cast<float>(startY - minY);
    printf("       实测弧顶抬高 = %.1f px（目标 %.1f）\n", peak, 34.0f);
    Check(peak > 20.0f && peak < 60.0f, "弧顶抬高度数在合理范围");

    delete pea;
}

// ---------------------------------------------------------------------------
// 测试 2：落点 x 精度
// ---------------------------------------------------------------------------
static void TestLandingX() {
    printf("\n[2] 落点 x 精度\n");

    Ballistic::Init();
    PVZ::Pea* pea = MakePea(100, 300, 9.0f);
    Ballistic::MakeArcing(pea, /*landX=*/300, /*groundY=*/380, 30.0f);

    for (int f = 0; f < 200 && !pea->mDead; ++f) {
        pea->mX += static_cast<int>(pea->mVelX);
        Ballistic::UpdateAll(true);
    }

    int dx = pea->mX - 300;
    printf("       落点 x = %d（目标 300，误差 %+d）\n", pea->mX, dx);

    // 每帧移动 9px，所以误差在 ±10px 内就算对
    Check(dx >= -10 && dx <= 10, "落点 x 误差 <= 10px");

    delete pea;
}

// ---------------------------------------------------------------------------
// 测试 3：不同 landX 都收敛
// ---------------------------------------------------------------------------
static void TestVaryingDistance() {
    printf("\n[3] 不同距离都能落地\n");

    int okCount = 0;
    const int dists[] = { 10, 45, 80, 150, 300, 600, 850 };

    for (int d : dists) {
        Ballistic::Init();
        PVZ::Pea* pea = MakePea(100, 300, 9.0f);
        Ballistic::MakeArcing(pea, 100 + d, 400, 34.0f);

        for (int f = 0; f < 400 && !pea->mDead; ++f) {
            pea->mX += static_cast<int>(pea->mVelX);
            Ballistic::UpdateAll(true);
        }

        bool ok = pea->mDead;
        printf("       距离 %3d px -> %s\n", d, ok ? "落地" : "*** 没落地 ***");
        if (ok) ++okCount;
        delete pea;
    }

    Check(okCount == static_cast<int>(sizeof(dists) / sizeof(dists[0])),
          "全部分布都能落地（含极近 10px 和极远 850px）");
}

// ---------------------------------------------------------------------------
// 测试 4：aim 的落点计算
// ---------------------------------------------------------------------------
static void TestAim() {
    printf("\n[4] 落点计算\n");

    PVZ::Plant plant{};
    memset(&plant, 0, sizeof(plant));
    plant.mX = 200;
    plant.mY = 300;
    plant.mRow = 2;

    // --- 情况 A：本行没僵尸 ---
    {
        PVZ::Board board{};
        memset(&board, 0, sizeof(board));
        board.mZombieList = nullptr;

        int lx = 0, gy = 0;
        bool found = Aim::ComputeMissLanding(&plant, &board, &lx, &gy);
        printf("       无僵尸: landX=%d groundY=%d found=%d\n", lx, gy, found);
        Check(!found, "无僵尸时返回 false");
        Check(lx - PVZ::PlantCenterX(&plant) == Aim::DEFAULT_LAND_PX,
              "无僵尸时用默认距离");
    }

    // --- 情况 B：本行有一个僵尸在 600 ---
    {
        PVZ::Board board{};
        memset(&board, 0, sizeof(board));

        PVZ::Zombie z{};
        memset(&z, 0, sizeof(z));
        z.mX = 600;
        z.mY = 300;
        z.mRow = 2;
        z.mHasHead = true;
        z.mDead = false;
        z.mNext = nullptr;
        board.mZombieList = &z;

        int lx = 0, gy = 0;
        bool found = Aim::ComputeMissLanding(&plant, &board, &lx, &gy);
        printf("       单僵尸@600: landX=%d (期望 %d)\n", lx, 600 - Aim::LAND_AHEAD_PX);
        Check(found, "找到僵尸返回 true");
        Check(lx == 600 - Aim::LAND_AHEAD_PX, "落在僵尸前方 LAND_AHEAD_PX");
    }

    // --- 情况 C：僵尸在别的行，不该被选中 ---
    {
        PVZ::Board board{};
        memset(&board, 0, sizeof(board));

        PVZ::Zombie z{};
        memset(&z, 0, sizeof(z));
        z.mX = 600;
        z.mRow = 4;          // 不同行
        z.mHasHead = true;
        board.mZombieList = &z;

        int lx = 0, gy = 0;
        bool found = Aim::ComputeMissLanding(&plant, &board, &lx, &gy);
        Check(!found, "别的行的僵尸不算数");
    }

    // --- 情况 D：多个僵尸，取最近的 ---
    {
        PVZ::Board board{};
        memset(&board, 0, sizeof(board));

        PVZ::Zombie far_z{}, near_z{};
        memset(&far_z, 0, sizeof(far_z));
        memset(&near_z, 0, sizeof(near_z));
        far_z.mX = 800;  far_z.mRow = 2;  far_z.mHasHead = true;  far_z.mNext = nullptr;
        near_z.mX = 450; near_z.mRow = 2; near_z.mHasHead = true; near_z.mNext = &far_z;
        board.mZombieList = &near_z;

        int lx = 0, gy = 0;
        Aim::ComputeMissLanding(&plant, &board, &lx, &gy);
        printf("       两僵尸 450/800: landX=%d (期望 %d)\n", lx, 450 - Aim::LAND_AHEAD_PX);
        Check(lx == 450 - Aim::LAND_AHEAD_PX, "取最近的那个");
    }

    // --- 情况 E：僵尸在植物身后，不该往左飞 ---
    {
        PVZ::Board board{};
        memset(&board, 0, sizeof(board));

        PVZ::Zombie z{};
        memset(&z, 0, sizeof(z));
        z.mX = 100;          // 在植物(200)左边
        z.mRow = 2;
        z.mHasHead = true;
        board.mZombieList = &z;

        int lx = 0, gy = 0;
        bool found = Aim::ComputeMissLanding(&plant, &board, &lx, &gy);
        printf("       身后僵尸@100: landX=%d found=%d\n", lx, found);
        Check(lx > PVZ::PlantCenterX(&plant), "落点仍在植物右侧（不会往左飞）");
    }

    // --- 情况 F：僵尸贴脸，要有最小距离 ---
    {
        PVZ::Board board{};
        memset(&board, 0, sizeof(board));

        PVZ::Zombie z{};
        memset(&z, 0, sizeof(z));
        z.mX = 250;          // 植物中心 240，僵尸只差 10px
        z.mRow = 2;
        z.mHasHead = true;
        board.mZombieList = &z;

        int lx = 0, gy = 0;
        Aim::ComputeMissLanding(&plant, &board, &lx, &gy);
        int dist = lx - PVZ::PlantCenterX(&plant);
        printf("       贴脸僵尸: landX=%d 距离=%d (最小 %d)\n",
               lx, dist, Aim::MIN_LAND_PX);
        Check(dist >= Aim::MIN_LAND_PX, "贴脸时仍满足最小飞行距离");
    }

    // --- 情况 G：死僵尸不算 ---
    {
        PVZ::Board board{};
        memset(&board, 0, sizeof(board));

        PVZ::Zombie z{};
        memset(&z, 0, sizeof(z));
        z.mX = 600;
        z.mRow = 2;
        z.mHasHead = true;
        z.mDead = true;      // 死了
        board.mZombieList = &z;

        int lx = 0, gy = 0;
        bool found = Aim::ComputeMissLanding(&plant, &board, &lx, &gy);
        Check(!found, "死僵尸被跳过");
    }

    // --- 情况 H：无头僵尸不算 ---
    {
        PVZ::Board board{};
        memset(&board, 0, sizeof(board));

        PVZ::Zombie z{};
        memset(&z, 0, sizeof(z));
        z.mX = 600;
        z.mRow = 2;
        z.mHasHead = false;   // 掉头了
        board.mZombieList = &z;

        int lx = 0, gy = 0;
        bool found = Aim::ComputeMissLanding(&plant, &board, &lx, &gy);
        Check(!found, "无头僵尸被跳过");
    }
}

// ---------------------------------------------------------------------------
// 测试 5：链表成环保护
// ---------------------------------------------------------------------------
static void TestCycleGuard() {
    printf("\n[5] 链表异常保护\n");

    Ballistic::Init();

    PVZ::Board board{};
    memset(&board, 0, sizeof(board));

    // 自己指向自己
    PVZ::Zombie z{};
    memset(&z, 0, sizeof(z));
    z.mX = 600;
    z.mRow = 2;
    z.mHasHead = true;
    z.mNext = &z;
    board.mZombieList = &z;

    PVZ::Plant plant{};
    memset(&plant, 0, sizeof(plant));
    plant.mX = 200;
    plant.mY = 300;
    plant.mRow = 2;

    int lx = 0, gy = 0;
    // 如果没做上限保护，这里会死循环
    Aim::ComputeMissLanding(&plant, &board, &lx, &gy);
    Check(true, "环状链表不会死循环");
}

// ---------------------------------------------------------------------------
// 测试 6：空指针健壮性
// ---------------------------------------------------------------------------
static void TestNullSafety() {
    printf("\n[6] 空指针健壮性\n");

    Ballistic::Init();

    int lx = 0, gy = 0;

    Aim::ComputeMissLanding(nullptr, nullptr, &lx, &gy);
    Check(true, "plant=nullptr 不崩");

    PVZ::Plant plant{};
    memset(&plant, 0, sizeof(plant));
    plant.mX = 200;
    plant.mY = 300;
    Aim::ComputeMissLanding(&plant, nullptr, &lx, &gy);
    Check(true, "board=nullptr 不崩");

    Ballistic::MakeArcing(nullptr, 100, 100, 10.0f);
    Check(true, "pea=nullptr 不崩");

    Ballistic::UpdateAll(true);
    Check(true, "UpdateAll(nullptr) 不崩");

    Check(Ballistic::Count() == 0, "空表时 Count()==0");
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------
int main() {
    printf("==== 抛物线数学离线自测 ====\n");

    TestArcShape();
    TestLandingX();
    TestVaryingDistance();
    TestAim();
    TestCycleGuard();
    TestNullSafety();

    printf("\n==== 结果：%d 通过 / %d 失败 ====\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
