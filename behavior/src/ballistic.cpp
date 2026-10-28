// ============================================================================
//  arc.cpp  ——  抛物线子弹系统实现
//
//  物理模型（逐帧离散积分，不是连续解）：
//
//      vy += gravity           每帧
//      y  += vy
//
//  给定起点 (spawnX, startY)、落点 (landX, groundY) 和弧顶抬高 arcPeak，
//  求 vy0 与 gravity。
//
//  设飞行帧数 T = |landX - spawnX| / |vx|，
//  设 y 轴向下为正。要让 y 先降到 startY - arcPeak 再升到 groundY：
//
//     dy = groundY - startY                  （通常 > 0，子弹往下掉）
//     A  = -arcPeak - dy                     （相对起点的位移，负 = 向上）
//
//  离散积分下 sum_{k=1..T} k = T(T+1)/2，所以：
//
//     A = T*vy0 + gravity * T(T+1)/2
//     顶点出现在 k ≈ -vy0/gravity 帧
//
//  这里采用**参数化简解**：先定 gravity，让顶点落在 T/2 处，
//  则 vy0 = -gravity * T / 2，代回得
//
//     gravity = -2*A / (T*T)     （A<0 时 gravity>0，符合直觉）
//
//  这个解在离散积分下顶点会略偏，但视觉上完全看不出来，
//  而且只要 T>=2 就永远不会算出 0 或负数。
// ============================================================================

#include "ballistic.h"
#include <cmath>
#include <cstdio>
#include <unordered_map>
#include <mutex>

namespace Ballistic {

namespace {

// 旁挂表：Pea* -> Track
std::unordered_map<PVZ::Pea*, Track> g_tracks;
std::mutex g_lock;

// 一份豌豆最多活多少帧就强制丢弃（防指针复活导致表膨胀）
constexpr uint32_t MAX_AGE = 600;

// 落点超出这个范围就当它已经离开屏幕
constexpr int SCREEN_RIGHT = 1200;

bool g_inited = false;

} // namespace

void Init() {
    std::lock_guard<std::mutex> lock(g_lock);
    g_tracks.clear();
    g_tracks.reserve(256);
    g_inited = true;
}

void MakeArcing(PVZ::Pea* pea, int landX, int groundY, float arcPeak) {
    if (!pea) return;

    // 水平速度必须是正的（向右飞）。原版豌豆 vx 约 +9.0。
    float vx = pea->mVelX;
    if (vx <= 0.01f) vx = 9.0f;

    int spawnX = pea->mX;
    int spawnY = pea->mY;

    float distF = static_cast<float>(landX - spawnX);
    if (distF <= 8.0f) {
        // 落点就在嘴边：直接平抛，两帧内掉下去
        distF = 8.0f;
    }

    float T = distF / vx;                 // 飞行帧数
    if (T < 2.0f) T = 2.0f;               // 至少 2 帧，否则公式退化

    float dy = static_cast<float>(groundY - spawnY);   // y 向下为正
    float A  = -arcPeak - dy;                          // 相对起点的总位移

    // gravity = -2A / T^2 ；A 大概率是负的 -> gravity 为正
    float gravity = -2.0f * A / (T * T);
    // 保底：保证至少是自由落体，别出现"往上飘"的怪事
    if (gravity < 0.05f) gravity = 0.05f;

    float vy0 = -gravity * T * 0.5f;      // 顶点落在 T/2 处

    Track t;
    t.spawnX  = spawnX;
    t.startY  = static_cast<float>(spawnY);
    t.landX   = landX;
    t.groundY = static_cast<float>(groundY);
    t.gravity = gravity;
    t.vy0     = vy0;
    t.age     = 0;

    std::lock_guard<std::mutex> lock(g_lock);
    g_tracks[pea] = t;

    // 立刻把初速写进去，让游戏自己那套 Pea::Update 也别把 y 拉回去
    pea->mVelY = vy0;
}

int UpdateAll(bool selfDestruct) {
    int landedCount = 0;

    std::lock_guard<std::mutex> lock(g_lock);
    if (g_tracks.empty()) return 0;

    for (auto it = g_tracks.begin(); it != g_tracks.end(); ) {
        PVZ::Pea* pea = it->first;
        Track& t = it->second;

        // 豌豆已经死了 / 指针作废 -> 丢
        if (!pea || pea->mDead) {
            it = g_tracks.erase(it);
            continue;
        }

        ++t.age;
        if (t.age > MAX_AGE) {
            it = g_tracks.erase(it);
            continue;
        }

        // 已经落地：等它自己被原版逻辑清掉，或者我们主动杀
        if (t.landed) {
            if (selfDestruct) {
                pea->mDead = true;
                it = g_tracks.erase(it);
                continue;
            }
            ++it;
            continue;
        }

        // --- 物理积分 ---
        t.vy0 += t.gravity;                  // 用 vy0 字段暂存当前 vy
        float ny = static_cast<float>(pea->mY) + t.vy0;

        // --- 落地判定 ---
        // 1) y 到达地面
        bool hitGround = (ny >= t.groundY);
        // 2) 或者横向已经飞过落点
        bool passedLand = (pea->mX >= t.landX);
        // 3) 或者飞出了右边界
        bool offscreen = (pea->mX > SCREEN_RIGHT);

        if (hitGround || passedLand || offscreen) {
            // 贴到地面，标记落地
            pea->mY     = static_cast<int>(t.groundY);
            pea->mVelY  = 0.0f;
            t.landed    = true;
            ++landedCount;

            if (selfDestruct) {
                pea->mDead = true;
                it = g_tracks.erase(it);
                continue;
            }
            ++it;
            continue;
        }

        // --- 写回 ---
        pea->mY    = static_cast<int>(ny);
        pea->mVelY = t.vy0;

        ++it;
    }

    return landedCount;
}

void Forget(PVZ::Pea* pea) {
    if (!pea) return;
    std::lock_guard<std::mutex> lock(g_lock);
    g_tracks.erase(pea);
}

int Count() {
    std::lock_guard<std::mutex> lock(g_lock);
    return static_cast<int>(g_tracks.size());
}

} // namespace Ballistic
