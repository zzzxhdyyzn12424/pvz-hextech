// ===========================================================================
//  findintegrate.cpp —— 找"位置 += 速度"的积分函数
//
//  判定：同一函数里同时出现
//      fadd/fsub/fsubr dword [reg+0x34]   (位置 X += 速度 X，或反之)
//      fadd/fsub/fsubr dword [reg+0x38]   (位置 Y += 速度 Y)
//  或者同一函数里既读 [reg+0x24]/[reg+0x2C]（速度）又 RMW [reg+0x34]/[reg+0x38]。
//
//  用法: findintegrate.exe <pid>
// ===========================================================================
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <psapi.h>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <vector>
#include <map>
#include <set>

struct Region { uint32_t base; size_t size; std::vector<uint8_t> data; };

struct Insn { uint32_t addr; const char* mnem; int ext; int reg; int disp; };

int main(int argc, char** argv) {
    if (argc < 2) { std::printf("usage: findintegrate.exe <pid>\n"); return 1; }
    DWORD pid = static_cast<DWORD>(std::strtoul(argv[1], nullptr, 10));

    HANDLE h = OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, FALSE, pid);
    if (!h) { std::printf("OpenProcess failed: %lu\n", GetLastError()); return 1; }
    HMODULE mods[1024]; DWORD need = 0;
    EnumProcessModulesEx(h, mods, sizeof(mods), &need, LIST_MODULES_ALL);
    MODULEINFO mi{};
    GetModuleInformation(h, mods[0], &mi, sizeof(mi));
    uint32_t base = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(mi.lpBaseOfDll));
    uint32_t end = base + mi.SizeOfImage;

    std::vector<Region> regions;
    uint32_t addr = base;
    while (addr < end) {
        MEMORY_BASIC_INFORMATION mbi;
        if (!VirtualQueryEx(h, reinterpret_cast<LPCVOID>(addr), &mbi, sizeof(mbi))) break;
        uint32_t rbase = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(mbi.BaseAddress));
        if (mbi.State == MEM_COMMIT && (mbi.Protect & (PAGE_EXECUTE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE))) {
            Region r; r.base = rbase; r.size = mbi.RegionSize; r.data.resize(r.size);
            SIZE_T got = 0;
            if (ReadProcessMemory(h, reinterpret_cast<LPCVOID>(rbase), r.data.data(), r.size, &got) && got) {
                r.size = got; r.data.resize(got); regions.push_back(std::move(r));
            }
        }
        addr = rbase + static_cast<uint32_t>(mbi.RegionSize);
    }
    CloseHandle(h);

    static const char* const R32N[8] = {"eax","ecx","edx","ebx","esp","ebp","esi","edi"};
    static const char* const FP[8] = {"fadd","fmul","fcom","fcomp","fsub","fsubr","fdiv","fdivr"};

    // 收集所有 RMW（fadd/fsub/fsubr）在 +0x20..+0x40 范围的指令
    std::map<uint32_t, std::vector<Insn>> byfunc;
    for (const auto& r : regions) {
        for (size_t i = 0; i + 2 < r.size; ++i) {
            uint8_t op = r.data[i], mrm = r.data[i+1];
            if (op != 0xD8 && op != 0xDC) continue;      // 单精度 dword / 双精度 qword
            if ((mrm & 0xC0) != 0x40) continue;           // mod==01 disp8
            int reg = mrm & 7, ext = (mrm >> 3) & 7;
            if (reg == 4) continue;
            if (ext != 0 && ext != 4 && ext != 5) continue;   // fadd/fsub/fsubr
            int disp = static_cast<int8_t>(r.data[i+2]);
            if (disp < 0x20 || disp > 0x40) continue;
            uint32_t at = r.base + static_cast<uint32_t>(i);
            // 回溯函数头
            uint32_t off = at - r.base;
            uint32_t fn = 0;
            for (int back = 1; back < 0x3000 && off >= static_cast<uint32_t>(back) + 3; ++back) {
                uint32_t p = off - back;
                if (r.data[p] == 0x55 && r.data[p+1] == 0x8B && r.data[p+2] == 0xEC) { fn = r.base + p; break; }
            }
            if (!fn) continue;
            byfunc[fn].push_back({at, FP[ext], ext, reg, disp});
        }
    }

    std::printf("扫描 %zu 个可执行段，%zu 个函数含 [reg+0x20..0x40] 的浮点 RMW\n\n", regions.size(), byfunc.size());

    for (const auto& kv : byfunc) {
        // 按寄存器分组，找同一寄存器上同时有 +0x34 和 +0x38 的
        std::map<int, std::set<int>> regdisps;
        for (const auto& in : kv.second) regdisps[in.reg].insert(in.disp);
        bool ok = false;
        int reg = -1;
        for (const auto& rd : regdisps) {
            if (rd.second.count(0x34) && (rd.second.count(0x38) || rd.second.count(0x3C))) {
                ok = true; reg = rd.first;
                break;
            }
        }
        if (!ok) continue;
        std::printf("=== func@0x%08X (RVA 0x%06X)  reg=%s ===\n", kv.first, kv.first - base, R32N[reg]);
        for (const auto& in : kv.second) {
            if (in.reg != reg) continue;
            std::printf("    %s 0x%08X  %-6s [%s+0x%02X]\n", "", in.addr, in.mnem, R32N[in.reg], in.disp);
        }
    }
    return 0;
}
