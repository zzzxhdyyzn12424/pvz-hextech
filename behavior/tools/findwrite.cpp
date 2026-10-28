// ===========================================================================
//  findwrite.cpp —— 在全模块可执行段里搜"写某个偏移"的指令
//
//  用法:  findwrite.exe <pid> <hex_offset> [reg]
//  例:    findwrite.exe 1234 38 ebx
//
//  会打印所有形如：
//      D9 5B 38        fstp  dword [ebx+0x38]
//      D8 43 38        fadd  dword [ebx+0x38]
//      D9 43 38        fld   dword [ebx+0x38]
//  ... 以及对应的函数头（回溯到 55 8B EC）。
//
//  用这个可以直接锁定 Pea 的 Y 积分代码。
// ===========================================================================
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <psapi.h>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <vector>

struct Region { uint32_t base; size_t size; std::vector<uint8_t> data; };

static const char* const R8N[8] = {"eax","ecx","edx","ebx","esp","ebp","esi","edi"};
static const char* const R32N[8] = {"eax","ecx","edx","ebx","esp","ebp","esi","edi"};

int main(int argc, char** argv) {
    if (argc < 3) {
        std::printf("usage: findwrite.exe <pid> <hex_offset> [reg]\n");
        std::printf("  reg: eax ecx edx ebx esp ebp esi edi  (default: any)\n");
        return 1;
    }
    DWORD pid = static_cast<DWORD>(std::strtoul(argv[1], nullptr, 10));
    int target_off = static_cast<int>(std::strtol(argv[2], nullptr, 16));
    int want_reg = -1;
    if (argc >= 4) {
        for (int i = 0; i < 8; ++i) if (!_stricmp(argv[3], R8N[i])) want_reg = i;
    }

    HANDLE h = OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, FALSE, pid);
    if (!h) { std::printf("OpenProcess failed: %lu\n", GetLastError()); return 1; }
    HMODULE mods[1024]; DWORD need = 0;
    if (!EnumProcessModulesEx(h, mods, sizeof(mods), &need, LIST_MODULES_ALL)) {
        std::printf("EnumProcessModulesEx failed\n"); CloseHandle(h); return 1;
    }
    HMODULE mainMod = mods[0];
    MODULEINFO mi{};
    GetModuleInformation(h, mainMod, &mi, sizeof(mi));
    uint32_t base = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(mi.lpBaseOfDll));
    uint32_t msize = mi.SizeOfImage;

    std::vector<Region> regions;
    uint32_t addr = base;
    uint32_t end = base + msize;
    while (addr < end) {
        MEMORY_BASIC_INFORMATION mbi;
        if (!VirtualQueryEx(h, reinterpret_cast<LPCVOID>(addr), &mbi, sizeof(mbi))) break;
        uint32_t rbase = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(mbi.BaseAddress));
        if (mbi.Type == MEM_IMAGE && (mbi.Protect & (PAGE_EXECUTE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE))) {
            Region r;
            r.base = rbase;
            r.size = mbi.RegionSize;
            r.data.resize(r.size);
            SIZE_T got = 0;
            if (ReadProcessMemory(h, reinterpret_cast<LPCVOID>(rbase), r.data.data(), r.size, &got) && got) {
                r.size = got;
                r.data.resize(got);
                regions.push_back(std::move(r));
            }
        }
        addr = rbase + static_cast<uint32_t>(mbi.RegionSize);
    }

    std::printf("模块基址 0x%08X, 可执行段 %zu 个\n\n", base, regions.size());

    auto backtrack = [&](uint32_t hit) -> uint32_t {
        for (const auto& r : regions) {
            if (hit < r.base || hit >= r.base + r.size) continue;
            uint32_t off = hit - r.base;
            for (int back = 1; back < 0x3000 && off >= static_cast<uint32_t>(back) + 3; ++back) {
                uint32_t p = off - back;
                if (r.data[p] == 0x55 && r.data[p+1] == 0x8B && r.data[p+2] == 0xEC) {
                    return r.base + p;
                }
            }
            break;
        }
        return 0;
    };

    int hits = 0;
    for (const auto& r : regions) {
        for (size_t i = 0; i + 2 < r.size; ++i) {
            uint8_t op = r.data[i], mrm = r.data[i+1], disp = r.data[i+2];
            if (static_cast<int>(static_cast<int8_t>(disp)) != target_off) continue;
            if ((mrm & 0xC0) != 0x40) continue;      // mod==01 (disp8)
            int reg = mrm & 7;
            int ext = (mrm >> 3) & 7;
            if (reg == 4) continue;                   // SIB，跳过
            if (want_reg >= 0 && reg != want_reg) continue;

            const char* mn = nullptr;
            const char* how = "";
            if (op == 0xD9 && ext == 0) { mn = "fld";  how = "READ"; }
            else if (op == 0xD9 && ext == 2) { mn = "fst";  how = "WRITE"; }
            else if (op == 0xD9 && ext == 3) { mn = "fstp"; how = "WRITE"; }
            else if (op == 0xD8 && ext == 0) { mn = "fadd"; how = "RMW"; }
            else if (op == 0xD8 && ext == 1) { mn = "fmul"; how = "RMW"; }
            else if (op == 0xD8 && ext == 4) { mn = "fsub"; how = "RMW"; }
            else if (op == 0xD8 && ext == 5) { mn = "fsubr"; how = "RMW"; }
            else if (op == 0xD8 && ext == 6) { mn = "fdiv"; how = "RMW"; }
            else if (op == 0xD8 && ext == 7) { mn = "fdivr"; how = "RMW"; }
            else if (op == 0xD8 && ext == 2) { mn = "fcom"; how = "READ"; }
            else if (op == 0xD8 && ext == 3) { mn = "fcomp"; how = "READ"; }
            if (!mn) continue;

            uint32_t at = r.base + static_cast<uint32_t>(i);
            uint32_t fn = backtrack(at);
            std::printf("0x%08X (RVA 0x%06X)  %s %-5s dword [%s%+d]   func@0x%08X (RVA 0x%06X)\n",
                        at, at - base, how, mn, R32N[reg], target_off,
                        fn, fn ? fn - base : 0);
            ++hits;
        }
    }
    std::printf("\n共 %d 处\n", hits);
    CloseHandle(h);
    return 0;
}
