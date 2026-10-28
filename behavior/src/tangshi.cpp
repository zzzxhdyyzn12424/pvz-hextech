// ============================================================================
//  tangshi.cpp  ——  唐诗射手 行为注入模块（阶段 1：探针 + 抛物线）
//
//  目标：PlantsVsZombies.exe (1.0.0.1051-zh), 32-bit
//
//  ## 这一版最核心的改动：不再猜偏移，改成运行时自动校准
//
//  之前 pvz_types.h 里的 Pea 字段偏移全是猜的，跟反汇编对不上。
//  继续猜下去只会浪费更多时间，而且一错就是崩游戏。
//
//  现在的做法：
//    钩住 0x00520AC0（每帧对每颗豌豆调一次，eax = 对象，edi = Board），
//    第一次见到某个对象时，扫描它 +0x08 开始的 0x60 字节，
//    用启发式认出三个 float：
//        vx  —— 每帧变化很小、量级 1~20
//        x   —— 每帧单调递增、量级 0~1500
//        y   —— 每帧几乎不变、量级 100~600
//    全部结果写进 tangshi.log，同时缓存下来直接用于物理积分。
//
//  ## 抛物线做法
//
//    原版豌豆 y 恒定、vx 恒定（直线飞行）。
//    我们每帧在钩子里做：
//        vy += gravity
//        y  += vy
//        if (y >= groundY) 置死
//    groundY 取"该行地面 y" —— 从同一行其他豌豆/僵尸的 y 学不出来，
//    所以直接用出生 y + 固定下沉量，具体见 cfg::FALL_PX。
//
//  状态：已编译。未真机验证。
// ============================================================================

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <psapi.h>
#include <cstdint>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <share.h>
#include <unordered_map>
#include <mutex>

#include "aob.h"
#include "aob_patterns.h"
#include "ballistic.h"
#include "aim.h"
#include "pvz_types.h"
#include "hook.h"

// ---------------------------------------------------------------------------
// 配置
// ---------------------------------------------------------------------------
namespace cfg {

constexpr float  EMPTY_CHANCE       = 0.40f;
constexpr int    DAMAGE_MULT        = 3;
constexpr int    MISS_THRESHOLD     = 10;
constexpr DWORD  RAGE_DURATION_MS   = 1500;
constexpr DWORD  RAGE_FIRE_GAP_MS   = 50;
constexpr int    EXPLODE_RADIUS_PX  = 120;
constexpr int    EXPLODE_DAMAGE     = 1800;
constexpr DWORD  FEAR_DURATION_MS   = 3000;
constexpr int    FEAR_SPEED_MULT    = 10;

// 空弹在多少帧内必落地（帧数，60fps 下约 0.75s）
constexpr int    FALL_FRAMES        = 45;
// 空弹最多飞多远就落地
constexpr int    FALL_MAX_DIST      = 170;
// 空弹往下掉多少像素就算砸到地面
constexpr float  FALL_DROP_PX       = 26.0f;

// 探针：前多少个不同对象 dump 到日志
constexpr int    PROBE_LIMIT        = 6;

} // namespace cfg

// ---------------------------------------------------------------------------
// 日志
//
//  坑：fopen / _fsopen 都不保证别的进程能同时读（_SH_DENYNO 在 mingw 的
//      msvcrt 上行为不一致，实测仍被独占）。所以这里彻底改用 Win32 API：
//      每次 Log() 都用 CreateFileA(..., FILE_SHARE_READ|WRITE)
//      打开 -> 写 -> 关。开销可忽略（只有探针阶段会打日志）。
// ---------------------------------------------------------------------------
static char   g_logPath[MAX_PATH] = {0};
static std::mutex g_logLock;

static void LogOpen() {
    std::lock_guard<std::mutex> lock(g_logLock);
    GetModuleFileNameA(nullptr, g_logPath, MAX_PATH);
    char* slash = strrchr(g_logPath, '\\');
    if (slash) *(slash + 1) = 0;
    strcat_s(g_logPath, sizeof(g_logPath), "tangshi.log");

    HANDLE h = CreateFileA(g_logPath, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                           nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h != INVALID_HANDLE_VALUE) CloseHandle(h);
}

static void Log(const char* fmt, ...) {
    std::lock_guard<std::mutex> lock(g_logLock);
    if (!g_logPath[0]) return;

    char line[1024];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(line, sizeof(line) - 2, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > static_cast<int>(sizeof(line)) - 2) n = sizeof(line) - 2;
    line[n]     = '\n';
    line[n + 1] = 0;

    HANDLE h = CreateFileA(g_logPath, FILE_APPEND_DATA,
                           FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                           OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    SetFilePointer(h, 0, nullptr, FILE_END);
    DWORD wrote = 0;
    WriteFile(h, line, static_cast<DWORD>(n + 1), &wrote, nullptr);
    CloseHandle(h);
}

// ---------------------------------------------------------------------------
// 运行时校准出来的 Pea 布局
// ---------------------------------------------------------------------------
struct PeaLayout {
    bool  valid   = false;
    int   offVX   = -1;    // float
    int   offVY   = -1;    // float（我们自己的，原版没有）
    int   offX    = -1;    // float
    int   offY    = -1;    // float
};

static PeaLayout g_layout;

// ---------------------------------------------------------------------------
// 每个 Pea 的旁挂状态
// ---------------------------------------------------------------------------
struct PeaExtra {
    int   bornX     = 0;
    float baseY     = 0.0f;
    float vy        = 0.0f;
    float gravity   = 0.0f;
    bool  arcing    = false;
    int   ticks     = 0;
};

static std::unordered_map<void*, PeaExtra> g_peaExtra;
static std::mutex g_peaMapLock;

// ---------------------------------------------------------------------------
// 植物 / 僵尸旁挂
// ---------------------------------------------------------------------------
struct PlantExtra {
    int   state     = 0;
    int   missCount = 0;
    int   hitCount  = 0;
    DWORD rageEnd   = 0;
    DWORD nextFire  = 0;
};

struct ZombieExtra {
    DWORD fearUntil = 0;
};

static std::unordered_map<void*, PlantExtra>  g_plantExtra;
static std::unordered_map<void*, ZombieExtra> g_zombieExtra;
static std::mutex g_mapLock;

// ---------------------------------------------------------------------------
// 全局运行态
// ---------------------------------------------------------------------------
static Hook::Instance g_hookPea;
static Hook::Instance g_hookObjUpdate;
static Hook::Instance g_hookDispatch;

typedef void (__stdcall *Fn_PeaRefresh)(void* self);
static Fn_PeaRefresh g_origPeaRefresh = nullptr;

// ---------------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------------
static float Rand01() {
    return static_cast<float>(rand()) / static_cast<float>(RAND_MAX);
}

static inline float RdF(const void* base, int off) {
    float v = 0.0f;
    memcpy(&v, static_cast<const uint8_t*>(base) + off, 4);
    return v;
}

static inline void WrF(void* base, int off, float v) {
    memcpy(static_cast<uint8_t*>(base) + off, &v, 4);
}

// ---------------------------------------------------------------------------
// 探针：把一个对象的前 0x60 字节按 float / int 双视图 dump 到日志
// ---------------------------------------------------------------------------
static void DumpObject(const void* obj, const char* why) {
    const uint8_t* p = static_cast<const uint8_t*>(obj);
    Log("---- dump %s @ %p ----", why, obj);
    for (int off = 0; off < 0x60; off += 4) {
        float f;
        int   i;
        memcpy(&f, p + off, 4);
        memcpy(&i, p + off, 4);
        // 只打印"像个数"的行，避免噪音
        bool fSane = (f == f) && (f > -1e6f) && (f < 1e6f) &&
                     !(f != 0.0f && (f > -1e-4f && f < 1e-4f));
        if (fSane || (i >= -100000 && i <= 100000)) {
            Log("  +0x%02X  int=%-12d  float=%.6g", off, i, f);
        }
    }
}

// ---------------------------------------------------------------------------
// 自动校准：从对象里认出 vx / x / y
//
//  前提（来自对 0x00520AC0 的反汇编）：
//    - 该函数每帧对每颗豌豆调一次
//    - 对象里至少有两个 float 坐标，其中一个每帧递增
//  策略：多帧采样，比较取值变化
// ---------------------------------------------------------------------------
struct Sample {
    void*  obj   = nullptr;
    int    frames = 0;
    float  prev[0x18] = {0};   // 按 +0x08 起的每 4 字节取样
    bool   primed = false;
    // 每个槽位的统计
    float  deltaSum[0x18] = {0};
    float  lastVal[0x18]  = {0};
    int    increasing[0x18] = {0};
    int    slots = 0;
};

static Sample g_sample;

static void TryCalibrate(void* obj) {
    if (g_layout.valid) return;

    constexpr int kBase = 0x08;
    constexpr int kSlots = 0x18;   // 0x08 .. 0x68

    if (g_sample.obj != obj) {
        g_sample.obj = obj;
        g_sample.frames = 0;
        g_sample.primed = false;
        g_sample.slots = kSlots;
        memset(g_sample.prev, 0, sizeof(g_sample.prev));
        memset(g_sample.deltaSum, 0, sizeof(g_sample.deltaSum));
        memset(g_sample.lastVal, 0, sizeof(g_sample.lastVal));
        memset(g_sample.increasing, 0, sizeof(g_sample.increasing));
        return;
    }

    const uint8_t* p = static_cast<const uint8_t*>(obj);

    if (!g_sample.primed) {
        for (int s = 0; s < kSlots; ++s) {
            memcpy(&g_sample.prev[s], p + kBase + s * 4, 4);
        }
        g_sample.primed = true;
        return;
    }

    for (int s = 0; s < kSlots; ++s) {
        float cur;
        memcpy(&cur, p + kBase + s * 4, 4);
        float d = cur - g_sample.prev[s];
        if (d > 0.05f && d < 60.0f) ++g_sample.increasing[s];
        g_sample.deltaSum[s] += d;
        g_sample.lastVal[s] = cur;
        g_sample.prev[s] = cur;
    }
    ++g_sample.frames;

    if (g_sample.frames < 8) return;

    // 选 x：递增次数最多的槽位，且量级像屏幕坐标
    int bestX = -1, bestInc = 0;
    for (int s = 0; s < kSlots; ++s) {
        float v = g_sample.lastVal[s];
        if (v < -50.0f || v > 2000.0f) continue;
        if (g_sample.increasing[s] > bestInc) {
            bestInc = g_sample.increasing[s];
            bestX = s;
        }
    }

    if (bestX >= 0 && bestInc >= g_sample.frames - 2) {
        g_layout.offX = kBase + bestX * 4;
        // y 取"递增次数接近 0、量级 100~700"的槽位
        int bestY = -1;
        float bestScore = 1e9f;
        for (int s = 0; s < kSlots; ++s) {
            if (s == bestX) continue;
            float v = g_sample.lastVal[s];
            if (v < 50.0f || v > 900.0f) continue;
            if (g_sample.increasing[s] > 3) continue;
            float score = static_cast<float>(g_sample.increasing[s]) * 100.0f +
                          fabsf(fabsf(g_sample.deltaSum[s]) - 0.0f);
            if (score < bestScore) { bestScore = score; bestY = s; }
        }
        if (bestY >= 0) g_layout.offY = kBase + bestY * 4;

        // vx：每帧变化稳定在 1~25 的槽位
        int bestVX = -1;
        for (int s = 0; s < kSlots; ++s) {
            if (s == bestX || s == bestY) continue;
            float avg = g_sample.deltaSum[s] / static_cast<float>(g_sample.frames);
            if (avg > 0.5f && avg < 30.0f) { bestVX = s; break; }
        }
        if (bestVX >= 0) g_layout.offVX = kBase + bestVX * 4;

        g_layout.valid = true;

        Log("");
        Log("======== 校准完成 ========");
        Log("  offX  = +0x%02X  (值 %.3f)", g_layout.offX, g_sample.lastVal[bestX]);
        Log("  offY  = +0x%02X  (值 %.3f)", g_layout.offY,
            bestY >= 0 ? g_sample.lastVal[bestY] : -1.0f);
        Log("  offVX = +0x%02X", g_layout.offVX);
        Log("  采样帧数 = %d", g_sample.frames);
        Log("==========================");
        Log("");
    }
}

// ---------------------------------------------------------------------------
// 钩子 A（主战场）：0x0051FCD0 —— 抛射物主更新，`ret 8` 双参数
//            [ebp+8] = owner/board   [ebp+12] = 对象指针
//
//  为什么是这个函数（证据链）：
//    1) 入口立刻读 [ebx+0x2C] / [ebx+0x30] / [ebx+0x24] / [ebx+0x28] 四个浮点，
//       其中 +0x24/+0x28 就是 x/y —— 一次取齐坐标，典型轨迹计算。
//    2) 调用者：0x0051F2A0（发射时） + 自身两处递归（0x00520661 / 0x005209F3，
//       碰撞后继续推进）。
//    3) 函数体 0x00520A1A 处 `cmp [eax],0x16` / `0x005209FB cmp [eax],0x14`
//       在读对象类型指针 —— 豌豆口径。
//    4) 尾部 `mov esp,ebp / pop ebp / ret 8` = __stdcall 双参数，栈传参。
//
//  ⚠ 之前钩 0x00520AC0 / 0x00520500 实测零命中：它们的外部调用点
//     @0x00415DAE 前面有 `cmp [eax+0x7F8],43 / jne` 的门，冒险模式根本走不到。
// ---------------------------------------------------------------------------
typedef void (__stdcall *Fn_PeaUpdate)(void* owner, void* self);
static Fn_PeaUpdate g_origPeaUpdate = nullptr;

static void __stdcall HookedPeaUpdate(void* owner, void* self) {
    {
        static volatile LONG s_calls = 0;
        LONG n = InterlockedIncrement(&s_calls);
        if (n <= 12) Log("[pea] call #%ld owner=%p self=%p", n, owner, self);
    }

    // 先跑原逻辑：它推进 x（水平匀速）并处理碰撞
    g_origPeaUpdate(owner, self);

    if (!self) return;

    PeaExtra* ex = nullptr;
    {
        std::lock_guard<std::mutex> lock(g_peaMapLock);
        auto it = g_peaExtra.find(self);
        if (it == g_peaExtra.end()) {
            PeaExtra ne;
            ne.arcing = (Rand01() < cfg::EMPTY_CHANCE);
            if (ne.arcing) {
                ne.vy      = -0.55f;
                ne.gravity = 0.030f + 0.015f * Rand01();
            }
            auto r = g_peaExtra.emplace(self, ne);
            ex = &r.first->second;

            static int probed = 0;
            if (probed < cfg::PROBE_LIMIT) {
                ++probed;
                Log("[pea] 新对象 type=%d", 0);
                DumpObject(self, "pea");
            }
        } else {
            ex = &it->second;
        }
    }
    if (!ex || !ex->arcing) return;

    float x = RdF(self, 0x24);
    float y = RdF(self, 0x28);
    if (x != x || y != y) return;

    if (ex->ticks == 0) {
        ex->bornX = static_cast<int>(x);
        ex->baseY = y;
    }
    ++ex->ticks;

    ex->vy += ex->gravity;
    float ny = y + ex->vy;

    int  dist = static_cast<int>(x) - ex->bornX;
    bool fell = (ny - ex->baseY > cfg::FALL_DROP_PX) ||
                (dist > cfg::FALL_MAX_DIST) ||
                (ex->ticks > cfg::FALL_FRAMES);

    if (fell) {
        WrF(self, 0x28, 900.0f);      // 推到画面外，下一帧被回收
        std::lock_guard<std::mutex> lock(g_peaMapLock);
        g_peaExtra.erase(self);
        return;
    }

    WrF(self, 0x28, ny);
}

// ---------------------------------------------------------------------------
// 钩子 B：0x00520AC0 —— 每帧绘制一个抛射物
//
//  ⚠ 这个函数**必须是 naked**：原函数的调用约定是"用 eax 传对象指针"，
//    而跳板里抄过来的前几条指令是
//        56           push esi
//        8B F0        mov esi, eax     ← 依赖进入时的 eax！
//        8B 07        mov eax, [edi]   ← 依赖进入时的 edi（board）
//    如果用普通 C 函数，prologue 会立刻破坏 eax/edi，
//    跳板执行时 mov esi,eax 拿到的是垃圾 → 崩或者读到野指针。
//
//  所以这里手工：pushal 保存全部寄存器 -> 取出 eax 当参数调 C 逻辑
//  -> popad 恢复 -> 直接 jmp 到跳板（尾跳，不返回）。
// ---------------------------------------------------------------------------
static void* g_peaTramp = nullptr;

extern "C" void Tangshi_OnPeaRefresh(void* self);

__attribute__((naked)) void __stdcall HookedPeaRefreshEntry() {
    __asm__ __volatile__(
        "pushal\n"                     // 保存 eax/ecx/edx/ebx/esp/ebp/esi/edi
        "pushl %%eax\n"                // 第 1 个参数 = 进入时的 eax（对象指针）
        "call _Tangshi_OnPeaRefresh\n"
        "addl $4, %%esp\n"
        "popal\n"                      // eax / edi 全部恢复成进入时的值
        "jmp *%0\n"                    // 尾跳到跳板，执行原指令后再跳回原函数
        :
        : "m"(g_peaTramp)
    );
}

// 只在绘制阶段打点 + 观察（不在这里改坐标，改坐标在 HookedPeaUpdate 里）
extern "C" void Tangshi_OnPeaRefresh(void* self) {
    static volatile LONG s_calls = 0;
    LONG n = InterlockedIncrement(&s_calls);
    if (n <= 6) Log("[draw] call #%ld self=%p", n, self);
    (void)self;
}

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------
static void SelfTestDump() {
    Log("=== 唐诗射手 %s ===", __DATE__);
    Log("PROBE_LIMIT=%d  EMPTY_CHANCE=%.2f", cfg::PROBE_LIMIT,
        static_cast<double>(cfg::EMPTY_CHANCE));
}

static bool Resolve() {
    int hits = 0;

    // ---- 钩子 A（主战场）：抛射物主更新，走栈参数，安全 ----
    void* pObj = Aob::FindInImage(AOB_PEA_MAIN_UPDATE, &hits);
    Log("[aob] AOB_PEA_MAIN_UPDATE -> %p  (命中 %d)", pObj, hits);
    if (pObj && hits == 1) {
        if (g_hookObjUpdate.Install(pObj,
                reinterpret_cast<const void*>(&HookedPeaUpdate))) {
            g_origPeaUpdate = reinterpret_cast<Fn_PeaUpdate>(g_hookObjUpdate.tramp);
            Log("[+] 钩子A（抛射物更新）已安装，跳板在 %p", g_hookObjUpdate.tramp);
        } else {
            Log("[x] 钩子A 安装失败");
        }
    } else {
        Log("[x] AOB_PEA_MAIN_UPDATE 命中数不是 1，落地逻辑不会生效");
    }

    // ---- 钩子 B（观察用）：抛射物绘制，eax 传参，必须 naked ----
    hits = 0;
    void* pPea = Aob::FindInImage(AOB_PEA_UPDATE, &hits);
    Log("[aob] AOB_PEA_UPDATE     -> %p  (命中 %d)", pPea, hits);
    if (pPea && hits == 1) {
        if (g_hookPea.Install(pPea,
                reinterpret_cast<const void*>(&HookedPeaRefreshEntry))) {
            g_origPeaRefresh = reinterpret_cast<Fn_PeaRefresh>(g_hookPea.tramp);
            g_peaTramp       = g_hookPea.tramp;
            Log("[+] 钩子B（绘制）已安装，跳板在 %p", g_hookPea.tramp);
        } else {
            Log("[x] 钩子B 安装失败");
        }
    }

    hits = 0;
    void* pDisp = Aob::FindInImage(AOB_BOARD_DISPATCH, &hits);
    Log("[aob] AOB_BOARD_DISPATCH  -> %p  (命中 %d)", pDisp, hits);

    return g_hookObjUpdate.active || g_hookPea.active;
}

static DWORD WINAPI InitThread(LPVOID) {
    LogOpen();
    SelfTestDump();

    // 等游戏主窗口
    for (int i = 0; i < 300; ++i) {
        if (FindWindowA(nullptr, "植物大战僵尸中文版")) break;
        Sleep(100);
    }
    Sleep(600);

    Ballistic::Init();

    if (!Resolve()) {
        Log("[x] 初始化失败，模块空转。游戏不受影响。");
        return 1;
    }

    Log("[+] 初始化完成。种一棵豌豆射手，然后看这个日志。");
    return 0;
}

BOOL APIENTRY DllMain(HMODULE hModule, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hModule);
        srand(GetTickCount());
        HANDLE t = CreateThread(nullptr, 0, InitThread, nullptr, 0, nullptr);
        if (t) CloseHandle(t);
    }
    return TRUE;
}
