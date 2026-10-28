// ============================================================================
//  aobfind.cpp  ——  特征码提取器（独立 32 位工具，不影响游戏）
//
//  用途：附加到正在运行的 PlantsVsZombies.exe，在它的可执行段里扫描
//        已知的指令片段，反推函数入口，生成可直接粘进 aob_patterns.h
//        的特征码字符串。
//
//  用法：
//      aobfind.exe              -> 自动找 processes 里唯一的 PlantsVsZombies.exe
//      aobfind.exe <pid>        -> 指定 PID
//      aobfind.exe --dump       -> 额外把命中区域的上下文反汇编视图打印出来
//
//  输出：stdout 打印四条候选特征码 + 命中数，直接复制到 aob_patterns.h。
//
//  设计原则：
//    1. 只读（PROCESS_VM_READ），不写游戏内存，绝不改游戏状态。
//    2. 每条特征码都带"命中数"统计，命中 > 1 会明确警告不够独特。
//    3. 相对跳转 / 绝对地址自动通配成 ??，保证重启游戏后仍然有效。
// ============================================================================

#include <windows.h>
#include <psapi.h>
#include <tlhelp32.h>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <vector>
#include <string>
#include <algorithm>

#pragma comment(lib, "psapi.lib")

// ---------------------------------------------------------------------------
//  进程 / 内存读取
// ---------------------------------------------------------------------------

static HANDLE       g_proc  = nullptr;
static uint32_t     g_base  = 0;      // 主模块基址
static uint32_t     g_size  = 0;      // 主模块大小

static bool ReadMem(uint32_t addr, void* buf, size_t len) {
    SIZE_T got = 0;
    return ReadProcessMemory(g_proc, reinterpret_cast<LPCVOID>(addr),
                             buf, len, &got) && got == len;
}

static DWORD FindGamePid() {
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return 0;
    PROCESSENTRY32 pe{};
    pe.dwSize = sizeof(pe);
    DWORD found = 0;
    if (Process32First(snap, &pe)) {
        do {
            if (_stricmp(pe.szExeFile, "PlantsVsZombies.exe") == 0) {
                found = pe.th32ProcessID;
                break;
            }
        } while (Process32Next(snap, &pe));
    }
    CloseHandle(snap);
    return found;
}

// ---------------------------------------------------------------------------
//  可执行段枚举
// ---------------------------------------------------------------------------

struct Region {
    uint32_t base;
    uint32_t size;
    std::vector<uint8_t> data;
};

static std::vector<Region> CollectExecRegions() {
    std::vector<Region> out;
    uint32_t addr = g_base;
    uint32_t end  = g_base + g_size;

    MEMORY_BASIC_INFORMATION mbi{};
    while (addr < end && VirtualQueryEx(g_proc,
            reinterpret_cast<LPCVOID>(addr), &mbi, sizeof(mbi)) == sizeof(mbi)) {
        const DWORD prot = mbi.Protect & 0xFF;
        const bool exec  = (prot == PAGE_EXECUTE ||
                            prot == PAGE_EXECUTE_READ ||
                            prot == PAGE_EXECUTE_READWRITE ||
                            prot == PAGE_EXECUTE_WRITECOPY);
        const bool ok    = !(mbi.Protect & PAGE_GUARD) &&
                           !(mbi.Protect & PAGE_NOACCESS);
        if (exec && ok && mbi.State == MEM_COMMIT) {
            Region r;
            r.base = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(mbi.BaseAddress));
            r.size = static_cast<uint32_t>(mbi.RegionSize);
            r.data.resize(r.size);
            SIZE_T got = 0;
            if (ReadProcessMemory(g_proc, mbi.BaseAddress, r.data.data(),
                                  r.size, &got) && got > 0) {
                r.size = static_cast<uint32_t>(got);
                r.data.resize(got);
                out.push_back(std::move(r));
            }
        }
        uint32_t next = static_cast<uint32_t>(
            reinterpret_cast<uintptr_t>(mbi.BaseAddress)) + static_cast<uint32_t>(mbi.RegionSize);
        if (next <= addr) break;
        addr = next;
    }
    return out;
}

// ---------------------------------------------------------------------------
//  扫描器：支持 ? 通配
// ---------------------------------------------------------------------------

struct Pattern {
    std::vector<uint8_t> bytes;
    std::vector<bool>    fixed;
};

static Pattern Parse(const char* s) {
    Pattern p;
    const char* q = s;
    auto hexv = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    };
    while (*q) {
        while (*q == ' ') ++q;
        if (!*q) break;
        if (q[0] == '?' && q[1] == '?') {
            p.bytes.push_back(0);
            p.fixed.push_back(false);
            q += 2;
        } else {
            int hi = hexv(q[0]), lo = hexv(q[1]);
            if (hi < 0 || lo < 0) return Pattern{};
            p.bytes.push_back(static_cast<uint8_t>((hi << 4) | lo));
            p.fixed.push_back(true);
            q += 2;
        }
    }
    return p;
}

// 返回所有命中地址（模块内 RVA 形式）
static std::vector<uint32_t> ScanAll(const std::vector<Region>& regions, const Pattern& p) {
    std::vector<uint32_t> hits;
    if (p.bytes.empty()) return hits;
    const size_t n = p.bytes.size();

    // 选第一个固定字节做锚点
    size_t anchor = 0;
    for (size_t i = 0; i < n; ++i) {
        if (p.fixed[i]) { anchor = i; break; }
    }

    for (const auto& r : regions) {
        if (r.size < n) continue;
        const uint8_t* b = r.data.data();
        const size_t limit = r.size - n;
        for (size_t i = 0; i <= limit; ++i) {
            if (b[i + anchor] != p.bytes[anchor]) continue;
            bool ok = true;
            for (size_t j = 0; j < n; ++j) {
                if (p.fixed[j] && b[i + j] != p.bytes[j]) { ok = false; break; }
            }
            if (ok) hits.push_back(r.base + static_cast<uint32_t>(i));
        }
    }
    return hits;
}

// ---------------------------------------------------------------------------
//  函数头回溯
//
//  x86 常见函数序言：
//      55              push ebp
//      8B EC           mov  ebp, esp
//      83 EC xx        sub  esp, xx
//      6A xx           push xx
//      68 xx xx xx xx  push imm32
//      53/56/57        push ebx/esi/edi
//
//  从命中点往前最多找 400 字节，返回最近的一个"看起来像函数头"的位置。
// ---------------------------------------------------------------------------

// 返回值越大越"像"函数入口，0 = 不像。
// 这样回溯时可以在 400 字节窗口里挑最像的那个，而不是撞到 ret 就放弃。
static int PrologueScore(const uint8_t* code) {
    // 6A FF 68 ?? ?? ?? ?? 64 A1 00 00 00 00
    //   push -1; push pScopeTable; mov eax, fs:[0]  —— MSVC 标准 SEH 序言
    if (code[0] == 0x6A && code[1] == 0xFF && code[2] == 0x68) return 100;
    // 55 8B EC                       push ebp; mov ebp, esp
    if (code[0] == 0x55 && code[1] == 0x8B && code[2] == 0xEC) return 95;
    // 8B FF 55 8B EC                 mov edi,edi; push ebp; mov ebp,esp（热补丁）
    if (code[0] == 0x8B && code[1] == 0xFF && code[2] == 0x55 &&
        code[3] == 0x8B && code[4] == 0xEC) return 98;
    // 83 EC xx 55 8B EC              先减栈再建帧
    if (code[0] == 0x83 && code[1] == 0xEC && code[3] == 0x55 &&
        code[4] == 0x8B && code[5] == 0xEC) return 90;
    // 6A ?? 68 ?? ?? ?? ??          push imm8; push imm32
    if (code[0] == 0x6A && code[2] == 0x68) return 80;
    // 56 8B F1                       push esi; mov esi, ecx（thiscall 裸函数）
    if (code[0] == 0x56 && code[1] == 0x8B && code[2] == 0xF1) return 85;
    // 53 8B D9   /  57 8B F9
    if ((code[0] == 0x53 && code[1] == 0x8B) ||
        (code[0] == 0x57 && code[1] == 0x8B)) return 60;
    // 55                              push ebp 单独出现
    if (code[0] == 0x55) return 50;
    return 0;
}

static const Region* FindRegion(const std::vector<Region>& regions, uint32_t addr) {
    for (const auto& r : regions) {
        if (addr >= r.base && addr < r.base + r.size) return &r;
    }
    return nullptr;
}

// 在命中的最多 0x600 字节前方挑"最像函数头"的位置。
// 优先取分数最高的；同分取离命中点最近的（最内层函数）。
static uint32_t BacktrackToPrologue(const std::vector<Region>& regions, uint32_t hit) {
    uint32_t best       = 0;
    int      bestScore  = 0;
    uint32_t bestDist   = 0;

    for (int back = 0; back < 0x600; ++back) {
        uint32_t cand = hit - back;
        const Region* r = FindRegion(regions, cand);
        if (!r) break;
        const uint32_t off = cand - r->base;
        if (off + 8 > r->size) continue;

        const int sc = PrologueScore(r->data.data() + off);
        if (sc <= 0) continue;

        // 更"像函数头"的赢；分数相同则更近的赢
        if (sc > bestScore || (sc == bestScore && back < bestDist)) {
            bestScore = sc;
            best      = cand;
            bestDist  = back;
        }
    }
    return best;
}

// ---------------------------------------------------------------------------
//  从函数头生成特征码
//
//  规则：抄 32 字节，把下面这些"每次重编译/每次安装都会变"的字节替换成 ??：
//    - 相对 call/jmp 的位移（E8/E9 后面 4 字节；EB 后面 1 字节）
//    - 相对条件跳转的位移（0F 8x 后面 4 字节；7x 后面 1 字节）
//    - push imm32 里的立即数（68 后面 4 字节）
//    - mov reg, imm32（B8..BF 后面 4 字节）
//
//  保守起见：位移和立即数一律通配，只保留操作码。
//  这样特征码可能变得不够独特，所以后面统计命中数并提示补强。
// ---------------------------------------------------------------------------

static std::string MakeSig(const std::vector<Region>& regions, uint32_t funcAddr, int len) {
    const Region* r = FindRegion(regions, funcAddr);
    if (!r) return "";
    const uint32_t off = funcAddr - r->base;
    if (off + len > r->size) return "";

    const uint8_t* code = r->data.data() + off;
    std::vector<bool> wild(len, false);

    for (int i = 0; i < len; ++i) {
        const uint8_t op = code[i];
        // E8/E9 rel32
        if ((op == 0xE8 || op == 0xE9) && i + 4 < len) {
            for (int k = 1; k <= 4; ++k) wild[i + k] = true;
            i += 4;
        }
        // EB rel8
        else if (op == 0xEB && i + 1 < len) {
            wild[i + 1] = true;
            i += 1;
        }
        // 0F 8x rel32
        else if (op == 0x0F && i + 5 < len && (code[i + 1] & 0xF0) == 0x80) {
            for (int k = 2; k <= 5; ++k) wild[i + k] = true;
            i += 5;
        }
        // 7x rel8
        else if ((op & 0xF0) == 0x70 && i + 1 < len) {
            wild[i + 1] = true;
            i += 1;
        }
        // 68 imm32
        else if (op == 0x68 && i + 4 < len) {
            for (int k = 1; k <= 4; ++k) wild[i + k] = true;
            i += 4;
        }
        // B8..BF imm32
        else if (op >= 0xB8 && op <= 0xBF && i + 4 < len) {
            for (int k = 1; k <= 4; ++k) wild[i + k] = true;
            i += 4;
        }
    }

    char buf[8];
    std::string out;
    for (int i = 0; i < len; ++i) {
        if (wild[i]) {
            out += "??";
        } else {
            std::snprintf(buf, sizeof(buf), "%02X", code[i]);
            out += buf;
        }
        if (i + 1 < len) out += ' ';
    }
    return out;
}

// ---------------------------------------------------------------------------
//  hex dump + 反汇编友好视图
// ---------------------------------------------------------------------------

static void DumpBytes(const std::vector<Region>& regions, uint32_t addr, int len) {
    const Region* r = FindRegion(regions, addr);
    if (!r) { std::printf("    <不可读>\n"); return; }
    const uint8_t* p = r->data.data() + (addr - r->base);
    for (int i = 0; i < len; i += 16) {
        std::printf("    +%02X: ", i);
        for (int j = 0; j < 16 && i + j < len; ++j) {
            std::printf("%02X ", p[i + j]);
        }
        std::printf("\n");
    }
}

// ---------------------------------------------------------------------------
//  函数体扫描：提取浮点指令访问的结构体偏移
//
//  不需要完整反汇编器 —— PVZ 是 MSVC 编译的 x86 代码，浮点访问几乎全部是
//  这几个固定形态（ModRM 的 mod=01，带 disp8）：
//
//      D9 /1  oo   fld   dword [reg+oo]
//      D8 /0  oo   fadd  dword [reg+oo]
//      D8 /1  oo   fmul  dword [reg+oo]
//      D8 /4  oo   fsub  dword [reg+oo]
//      D8 /5  oo   fsubr dword [reg+oo]
//      D8 /6  oo   fdiv  dword [reg+oo]
//      D9 /3  oo   fstp  dword [reg+oo]
//      DD /0  oo   fld   qword [reg+oo]
//      DD /3  oo   fstp  qword [reg+oo]
//
//  ModRM 在 0x40..0x7F 之间表示 [reg+disp8]（mod=01, rm=reg 编码直接寻址）。
//  所以只要看第二个字节是否落在 0x40..0x7F，偏移就是第三个字节。
// ---------------------------------------------------------------------------

struct FpInsn {
    uint32_t addr;
    std::string mnem;
    int         disp;
    int         reg;      // 0=eax 1=ecx 2=edx 3=ebx 4=esp 5=ebp 6=esi 7=edi
};

static const char* const kRegName[8] = {"eax","ecx","edx","ebx","esp","ebp","esi","edi"};

static std::vector<FpInsn> ScanFloatInsns(const std::vector<Region>& regions,
                                          uint32_t funcAddr, uint32_t funcEnd) {
    std::vector<FpInsn> out;
    const Region* r = FindRegion(regions, funcAddr);
    if (!r) return out;
    uint32_t off = funcAddr - r->base;
    uint32_t end = (std::min)(funcEnd - r->base, static_cast<uint32_t>(r->size));
    const uint8_t* b = r->data.data();

    for (uint32_t i = off; i + 2 < end; ++i) {
        const uint8_t op = b[i];
        const uint8_t mrm = b[i + 1];
        if (mrm < 0x40 || mrm > 0x7F) continue;      // 只要 [reg+disp8]
        const int reg = (mrm >> 0) & 7;
        const int ext = (mrm >> 3) & 7;              // 扩展操作码
        const int disp = static_cast<int8_t>(b[i + 2]);
        const char* mn = nullptr;

        if (op == 0xD9) {
            if (ext == 1) mn = "fld";
            else if (ext == 3) mn = "fstp";
        } else if (op == 0xD8) {
            switch (ext) {
                case 0: mn = "fadd";  break;
                case 1: mn = "fmul";  break;
                case 4: mn = "fsub";  break;
                case 5: mn = "fsubr"; break;
                case 6: mn = "fdiv";  break;
                default: break;
            }
        }
        if (mn) {
            out.push_back({r->base + i, mn, disp, reg});
        }
    }
    return out;
}

// 在函数里找"下一处函数头"作为函数体结束
static uint32_t GuessFuncEnd(const std::vector<Region>& regions, uint32_t funcAddr) {
    const Region* r = FindRegion(regions, funcAddr);
    if (!r) return funcAddr + 0x200;
    const uint32_t off = funcAddr - r->base;
    const uint8_t* b = r->data.data();
    // 找第一个 C2/C3(ret) / C7 0000 / CC 填充
    for (uint32_t i = off + 8; i + 8 < r->size && i < off + 0x2000; ++i) {
        if (b[i] == 0xC3 || b[i] == 0xC2) {
            // 后面连续 CC 说明函数结束
            int cc = 0;
            for (int k = 1; k <= 8 && i + k < r->size; ++k) {
                if (b[i + k] == 0xCC) ++cc;
            }
            if (cc >= 2) return r->base + i + 1;
        }
    }
    return funcAddr + 0x800;
}

static void PrintFuncAnalysis(const std::vector<Region>& regions,
                              uint32_t funcAddr, const char* tag) {
    if (!funcAddr) { std::printf("    <%s> 无函数头\n", tag); return; }
    uint32_t end = GuessFuncEnd(regions, funcAddr);
    auto ins = ScanFloatInsns(regions, funcAddr, end);

    std::printf("    <%s> func @0x%08X (RVA 0x%X)  长度约 %u 字节  浮点访问 %zu 条\n",
                tag, funcAddr, funcAddr - g_base, end - funcAddr, ins.size());

    // 统计各偏移出现次数，按偏移排序
    int count[256] = {0};
    for (const auto& f : ins) {
        if (f.disp >= 0 && f.disp < 256) ++count[f.disp];
    }
    std::printf("      偏移直方图: ");
    for (int d = 0; d < 256; ++d) {
        if (count[d]) std::printf("+%02X:%d ", d, count[d]);
    }
    std::printf("\n");

    // 按偏移分组打印，方便看出哪个偏移是"读多写少"（速度）哪个是"写多读少"（位置）
    for (int d = 0; d < 256; ++d) {
        if (!count[d]) continue;
        int reads = 0, writes = 0;
        for (const auto& f : ins) {
            if (f.disp != d) continue;
            if (f.mnem[0] == 'f' && f.mnem[1] == 's') ++writes;  // fst/fstp
            else ++reads;
        }
        std::printf("      [+%02X] %2d 次 (读 %d / 写 %d):", d, count[d], reads, writes);
        int n = 0;
        for (const auto& f : ins) {
            if (f.disp != d) continue;
            if (n++ >= 6) { std::printf(" ..."); break; }
            std::printf(" %s@%X", f.mnem, f.addr);
        }
        std::printf("\n");
    }
}

struct Candidate {
    std::string  name;
    std::string  probe;      // 用来定位的"内部指令片段"
    std::string  note;
    int          sigLen;
};

int main(int argc, char** argv) {
    SetConsoleOutputCP(65001);

    uint32_t pid = 0;
    bool dump = false;
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--dump") == 0) dump = true;
        else pid = static_cast<uint32_t>(std::atoi(argv[i]));
    }
    if (pid == 0) pid = FindGamePid();
    if (pid == 0) {
        std::printf("[x] 找不到 PlantsVsZombies.exe，先启动游戏，或手动传 PID。\n");
        return 1;
    }

    g_proc = OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION,
                         FALSE, pid);
    if (!g_proc) {
        std::printf("[x] OpenProcess 失败，错误码 %lu（可能需要管理员权限）\n", GetLastError());
        return 1;
    }

    HMODULE mods[1024];
    DWORD needed = 0;
    if (!EnumProcessModules(g_proc, mods, sizeof(mods), &needed) || needed < sizeof(HMODULE)) {
        std::printf("[x] EnumProcessModules 失败\n");
        return 1;
    }
    MODULEINFO mi{};
    if (!GetModuleInformation(g_proc, mods[0], &mi, sizeof(mi))) {
        std::printf("[x] GetModuleInformation 失败\n");
        return 1;
    }
    g_base = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(mi.lpBaseOfDll));
    g_size = mi.SizeOfImage;

    std::printf("================================================================\n");
    std::printf("  PVZ 特征码提取器\n");
    std::printf("  PID   : %u\n", pid);
    std::printf("  Base  : 0x%08X\n", g_base);
    std::printf("  Size  : 0x%X (%u 字节)\n", g_size, g_size);
    std::printf("================================================================\n\n");

    auto regions = CollectExecRegions();
    size_t total = 0;
    for (const auto& r : regions) total += r.size;
    std::printf("[*] 可执行段 %zu 个，共 %zu 字节\n\n", regions.size(), total);
    if (regions.empty()) {
        std::printf("[x] 没读到任何可执行段，检查权限\n");
        return 1;
    }

    // ---- 目标 1：Pea::Update（抛物线必须的） ----------------------------
    // 特征：同一条函数里同时出现 mVelX(0x24) 和 mVelY(0x28) 的浮点访问
    //   D9 46 24   fld  dword [esi+24]   /  D8 46 28  fadd dword [esi+28]
    //   D9 46 28   fld  dword [esi+28]
    // 也可能用 edi/ebx 寄存器，所以多试几种组合。

    std::printf("----------------------------------------------------------------\n");
    std::printf("[1] Pea::Update  —— 抛物线积分必须钩这里\n");
    std::printf("----------------------------------------------------------------\n");

    struct Probe { const char* sig; const char* desc; };
    const Probe peaProbes[] = {
        { "D9 46 24", "fld  dword [esi+24]    // mVelX" },
        { "D8 46 28", "fadd dword [esi+28]    // mVelY" },
        { "D9 46 28", "fld  dword [esi+28]    // mVelY" },
        { "D9 47 24", "fld  dword [edi+24]" },
        { "D8 47 28", "fadd dword [edi+28]" },
        { "D9 43 24", "fld  dword [ebx+24]" },
        { "D8 43 28", "fadd dword [ebx+28]" },
    };

    for (const auto& pr : peaProbes) {
        Pattern p = Parse(pr.sig);
        auto hits = ScanAll(regions, p);
        std::printf("  %-14s %-28s 命中 %zu\n", pr.sig, pr.desc, hits.size());
        if (dump && !hits.empty()) {
            int shown = 0;
            for (uint32_t h : hits) {
                if (shown++ >= 6) { std::printf("    ...共 %zu 处\n", hits.size()); break; }
                uint32_t fn = BacktrackToPrologue(regions, h);
                std::printf("    hit @0x%08X  func @0x%08X (RVA 0x%X)\n",
                            h, fn, fn - g_base);
                if (fn) DumpBytes(regions, fn, 48);
            }
        }
    }

    // ---- 目标 2：Plant::Update -----------------------------------------
    std::printf("\n----------------------------------------------------------------\n");
    std::printf("[2] Plant::Update  —— 射击冷却 / 红温计时\n");
    std::printf("----------------------------------------------------------------\n");
    const Probe plantProbes[] = {
        { "83 7E 28 00",  "cmp dword [esi+28],0   // mPlantType == 0 ?" },
        { "83 7E 50 00",  "cmp dword [esi+50],0   // mShootCooldown" },
        { "8B 46 50",     "mov eax,[esi+50]" },
        { "FF 4E 50",     "dec dword [esi+50]" },
        { "8B 8E 90 00 00 00", "mov ecx,[esi+90]" },
    };
    for (const auto& pr : plantProbes) {
        Pattern p = Parse(pr.sig);
        auto hits = ScanAll(regions, p);
        std::printf("  %-22s %-34s 命中 %zu\n", pr.sig, pr.desc, hits.size());
        if (dump && !hits.empty()) {
            int shown = 0;
            for (uint32_t h : hits) {
                if (shown++ >= 6) { std::printf("    ...共 %zu 处\n", hits.size()); break; }
                uint32_t fn = BacktrackToPrologue(regions, h);
                std::printf("    hit @0x%08X  func @0x%08X\n", h, fn);
                if (fn) DumpBytes(regions, fn, 48);
            }
        }
    }

    // ---- 目标 3：Zombie::Update ----------------------------------------
    std::printf("\n----------------------------------------------------------------\n");
    std::printf("[3] Zombie::Update  —— 逃跑 10 倍速\n");
    std::printf("----------------------------------------------------------------\n");
    const Probe zombieProbes[] = {
        { "83 7E 24 00",  "cmp dword [esi+24],0   // mZombieType" },
        { "D9 46 08",     "fld dword [esi+8]      // mX" },
        { "D9 46 44",     "fld dword [esi+44]     // mSpeed" },
        { "D8 4E 44",     "fmul dword [esi+44]    // *= mSpeed" },
    };
    for (const auto& pr : zombieProbes) {
        Pattern p = Parse(pr.sig);
        auto hits = ScanAll(regions, p);
        std::printf("  %-22s %-34s 命中 %zu\n", pr.sig, pr.desc, hits.size());
        if (dump && !hits.empty()) {
            int shown = 0;
            for (uint32_t h : hits) {
                if (shown++ >= 6) { std::printf("    ...共 %zu 处\n", hits.size()); break; }
                uint32_t fn = BacktrackToPrologue(regions, h);
                std::printf("    hit @0x%08X  func @0x%08X\n", h, fn);
                if (fn) DumpBytes(regions, fn, 48);
            }
        }
    }

    // ---- 目标 4：AddPea ------------------------------------------------
    std::printf("\n----------------------------------------------------------------\n");
    std::printf("[4] Board::AddPea  —— 判空枪 / 改伤害\n");
    std::printf("----------------------------------------------------------------\n");
    const Probe addProbes[] = {
        { "6A 3C E8", "push 0x3C; call new   // sizeof(Pea)==0x3C ?" },
        { "6A 40 E8", "push 0x40; call new" },
        { "6A 44 E8", "push 0x44; call new" },
        { "6A 48 E8", "push 0x48; call new" },
    };
    for (const auto& pr : addProbes) {
        Pattern p = Parse(pr.sig);
        auto hits = ScanAll(regions, p);
        std::printf("  %-12s %-32s 命中 %zu\n", pr.sig, pr.desc, hits.size());
        if (dump && !hits.empty()) {
            int shown = 0;
            for (uint32_t h : hits) {
                if (shown++ >= 8) { std::printf("    ...共 %zu 处\n", hits.size()); break; }
                std::printf("    hit @0x%08X (RVA 0x%X)\n", h, h - g_base);
            }
        }
    }

    std::printf("\n================================================================\n");
    std::printf("  函数体浮点偏移分析（判断哪个才是 Pea::Update）\n");
    std::printf("================================================================\n");

    struct Anchor { uint32_t hit; const char* why; };
    const Anchor anchors[] = {
        { 0x00462435, "D8 46 28 fadd [esi+28]  #1" },
        { 0x00528FFA, "D8 46 28 fadd [esi+28]  #2" },
        { 0x005209AF, "D8 47 28 fadd [edi+28]  唯一" },
        { 0x00431074, "D9 46 44 fld  [esi+44]  唯一(僵尸速度)" },
        { 0x00517245, "83 7E 50 00 cmp [esi+50] 唯一(射击冷却)" },
        { 0x0051EA37, "83 7E 24 00 僵尸类型 switch" },
    };
    for (const auto& a : anchors) {
        uint32_t fn = BacktrackToPrologue(regions, a.hit);
        std::printf("\n  --- %s @0x%08X ---\n", a.why, a.hit);
        PrintFuncAnalysis(regions, fn, "候选");
    }

    std::printf("\n================================================================\n");
    std::printf("  扫描完成。下一步：把 dump 出来的函数头机器码挑出来，\n");
    std::printf("  用 --dump 再看一遍上下文，确认是目标函数后填进 aob_patterns.h。\n");
    std::printf("================================================================\n");

    CloseHandle(g_proc);
    return 0;
}
