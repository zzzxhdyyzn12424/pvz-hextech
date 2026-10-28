// ===========================================================================
//  findcall.cpp —— 搜谁调用了某个函数（E8 rel32 / FF15 间接）
//  用法: findcall.exe <pid> <target_hex>
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

int main(int argc, char** argv) {
    if (argc < 3) { std::printf("usage: findcall.exe <pid> <target_hex>\n"); return 1; }
    DWORD pid = static_cast<DWORD>(std::strtoul(argv[1], nullptr, 10));
    uint32_t target = static_cast<uint32_t>(std::strtoul(argv[2], nullptr, 16));

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

    auto backtrack = [&](uint32_t hit) -> uint32_t {
        for (const auto& r : regions) {
            if (hit < r.base || hit >= r.base + r.size) continue;
            uint32_t off = hit - r.base;
            for (int back = 1; back < 0x3000 && off >= static_cast<uint32_t>(back) + 3; ++back) {
                uint32_t p = off - back;
                if (r.data[p] == 0x55 && r.data[p+1] == 0x8B && r.data[p+2] == 0xEC) return r.base + p;
            }
            break;
        }
        return 0;
    };

    std::printf("搜索对 0x%08X 的直接 call (E8 rel32)\n\n", target);
    int hits = 0;
    for (const auto& r : regions) {
        for (size_t i = 0; i + 5 <= r.size; ++i) {
            if (r.data[i] != 0xE8) continue;
            int32_t rel = *reinterpret_cast<const int32_t*>(&r.data[i+1]);
            uint32_t at = r.base + static_cast<uint32_t>(i);
            if (at + 5 + rel == target) {
                uint32_t fn = backtrack(at);
                std::printf("call @0x%08X (RVA 0x%06X)  in func@0x%08X (RVA 0x%06X)\n",
                            at, at - base, fn, fn ? fn - base : 0);
                ++hits;
            }
        }
    }
    std::printf("\n共 %d 处\n", hits);
    CloseHandle(h);
    return 0;
}
