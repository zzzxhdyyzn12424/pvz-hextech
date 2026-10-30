// ============================================================================
//  tuning.h —— 数值表
//
//  这些数全部照抄 tools/hextech_core.py 的 Engine 类常量（外置版已经在游戏里
//  被玩了几十个版本，数值是调过的）。改数值请两边一起改，别只改这里。
//
//  每条后面标注了 Python 侧的同名常量，方便对照。
// ============================================================================
#pragma once

namespace hx {
namespace tune {

// ---- 帧钟 ----
// 时间基准用**真实毫秒**（GetTickCount），和外置版的 time.time() 一致。
//
// 为什么不数帧：我原本以为 Board::Update 每秒正好 100 次，可以拿自己的
// 帧计数器当钟用。实测不成立 —— 同一段日志里两次采样之间的帧数在
// 95~800 之间乱跳（说明 0x539D30 这个 thunk 并不是每次 Update 只调一次）。
// 拿它当钟，「逃跑计划 5 秒衰减」会变成 1 秒、「山脉龙魂 3 秒回盾」会变成
// 不到 1 秒，全是玩家能立刻感觉到的错。
//
// 移速补位移不受影响：那是按「游戏自己走了多少」按比例补的，调几次都对。
constexpr uint32_t kEscapeDecayMs = 5000;           // Z_ESCAPE_DECAY = 5.0s

// ---- 植物攻速（改 +0x5C 原生射击间隔，游戏自己多生成子弹）----
constexpr double P_SWIFT_SPEED   = 0.25;            // P_SWIFT_SPEED
constexpr double P_ZEALOT_SPEED  = 0.15;            // P_ZEALOT_SPEED
constexpr double P_RAGEBLADE_PER_HIT = 0.01;        // P_RAGEBLADE_PER_HIT
constexpr double P_RAGEBLADE_CAP = 1.00;            // P_RAGEBLADE_CAP
constexpr double P_BIGPEA_SLOW   = 0.875;           // P_BIGPEA_SLOW
constexpr int    P_SHOOT_INT_MIN = 1;               // P_SHOOT_INTERVAL_MIN
constexpr int    kShootNativeMin = 140;             // SHOOT_NATIVE_MIN
constexpr int    kShootNativeMax = 400;             // SHOOT_NATIVE_MAX

// ---- 植物血量 ----
constexpr int    P_TITAN_HP   = 2;                  // P_TITAN_HP（+200% = ×3）
constexpr int    P_FISH_HP    = 80000;              // P_FISH_HP
constexpr double P_MOUNTAIN_RATIO = 0.30;           // P_MOUNTAIN_RATIO
constexpr uint32_t P_MOUNTAIN_REGEN_MS = 3000;      // P_MOUNTAIN_REGEN = 3.0s

// ---- 僵尸血量 / 护盾 ----
constexpr double Z_GOLIATH_HP = 1.00;               // Z_GOLIATH_HP（+100% = ×2）
constexpr int    Z_ASTRAL_HP  = 1800;               // Z_ASTRAL_HP
constexpr double Z_ESCAPE_RATIO  = 0.50;            // Z_ESCAPE_RATIO
constexpr double Z_ESCAPE_SHIELD = 1.00;            // Z_ESCAPE_SHIELD
// 护盾类型：1 = 铁门（正面挡豌豆，最像「护盾」）。2 = 梯子、3 = 报纸。
// 借用的是僵尸原生护盾槽，所以护盾吸收完全由游戏自己算，我们一帧都不用补。
constexpr int    Z_SHIELD_TYPE = 1;                 // Z_SHIELD_TYPE

// ---- 僵尸移速（补位移，不写 +0x34）----
constexpr double Z_ESCAPE_SPEED    = 2.00;          // Z_ESCAPE_SPEED
constexpr double Z_LONE_SPEED      = 1.00;          // Z_LONE_SPEED
constexpr double Z_HOMEGUARD_SPEED = 1.00;          // Z_HOMEGUARD_SPEED
constexpr double Z_LEGDAY_SPEED    = 0.50;          // Z_LEGDAY_SPEED
constexpr double Z_MADMAN_SPEED    = 1.50;          // Z_MADMAN_SPEED
constexpr double Z_SMALLKILLER_SPEED = 1.00;        // Z_SMALLKILLER_SPEED
constexpr double Z_BARON_SPEED     = 0.50;          // Z_BARON_SPEED
constexpr int    Z_LONE_RANGE_X    = 85;            // Z_LONE_RANGE_X
constexpr int    Z_LONE_RANGE_Y    = 100;           // Z_LONE_RANGE_Y
// 补位移时的保护：单帧补的量不能超过这个，否则僵尸会瞬移。
// 正常一帧走 0.2~0.4 px，×4 倍速也就 1.6 px，3 px 足够宽松又能挡住脏数据。
constexpr double Z_NUDGE_MAX_PER_FRAME = 3.0;

// ---- 僵尸体型（改 GameObject 宽高，影响碰撞盒；视觉大小不变）----
// PVZ 的绘制尺寸由 reanim 自带，对象里没有可直接写的缩放字段（已扫过）。
constexpr double Z_MADMAN_SCALE      = 0.60;        // Z_MADMAN_SCALE
constexpr double Z_SMALLKILLER_SCALE = 0.60;        // Z_SMALLKILLER_SCALE

}  // namespace tune
}  // namespace hx
