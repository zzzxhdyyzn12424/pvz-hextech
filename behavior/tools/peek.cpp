// ===========================================================================
//  peek.cpp —— 读取进程内存并 dump
//  用法:
//    peek.exe <pid> <addr> <count>            读 count 个 u32
//    peek.exe <pid> <addr> --float <count>    当作 float 读
// ===========================================================================
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <vector>

int main(int argc, char** argv) {
    if (argc < 4) {
        std::printf("usage: peek.exe <pid> <addr_hex> [--float|--u32|--bytes] <count>\n");
        return 1;
    }
    DWORD pid = static_cast<DWORD>(std::strtoul(argv[1], nullptr, 10));
    uint32_t addr = static_cast<uint32_t>(std::strtoul(argv[2], nullptr, 16));
    bool as_float = false, as_bytes = false;
    int argi = 3;
    if (!_stricmp(argv[3], "--float")) { as_float = true; argi = 4; }
    else if (!_stricmp(argv[3], "--bytes")) { as_bytes = true; argi = 4; }
    int count = (argc > argi) ? std::atoi(argv[argi]) : 16;

    HANDLE h = OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, FALSE, pid);
    if (!h) { std::printf("OpenProcess failed: %lu\n", GetLastError()); return 1; }

    size_t bytes = as_bytes ? std::strtoul(argv[argi], nullptr, 16) : static_cast<size_t>(count) * 4;
    if (as_bytes) count = static_cast<int>(bytes);
    std::vector<uint8_t> buf(bytes, 0);
    SIZE_T got = 0;
    if (!ReadProcessMemory(h, reinterpret_cast<LPCVOID>(addr), buf.data(), bytes, &got)) {
        std::printf("ReadProcessMemory failed @0x%08X: %lu\n", addr, GetLastError());
        CloseHandle(h); return 1;
    }
    CloseHandle(h);

    if (as_bytes) {
        for (int i = 0; i < count; ++i) {
            if (i % 16 == 0) std::printf("\n%08X  ", addr + i);
            std::printf("%02X ", buf[i]);
        }
        std::printf("\n");
        return 0;
    }

    for (int i = 0; i < count; ++i) {
        if (i % 4 == 0) std::printf("\n+0x%04X:", i * 4);
        uint32_t v;
        std::memcpy(&v, buf.data() + i * 4, 4);
        if (as_float) {
            float f;
            std::memcpy(&f, &v, 4);
            std::printf("  %08X=%-13.4f", v, f);
        } else {
            std::printf("  %08X", v);
        }
    }
    std::printf("\n");
    return 0;
}
