// ============================================================================
//  aob.cpp  ——  特征码扫描实现
//
//  特征码字符串格式：十六进制字节用空格分隔，通配符写 "??"
//     "55 8B EC 83 EC 10 53 ?? ?? FF"
//
//  扫描前自动把这串文本编译成 (bytes, mask)，然后在模块可执行段里找。
//  为了速度，用"最稀有固定字节"做预筛锚点。
//
//  ## 为什么要 FindInImage 而不是只传 base/size
//
//  游戏镜像里 .text 之外还有别的可执行映射（尤其 32 位老程序），
//  而且 VirtualQuery 拿到的 protection 比 SizeOfImage 更可靠。
//  逐页扫能避开读取不可读页导致的访问异常。
// ============================================================================

#include "aob.h"
#include <windows.h>
#include <psapi.h>
#include <cstring>
#include <vector>
#include <cstdio>

namespace Aob {

namespace {

struct CompiledSig {
    std::vector<uint8_t> bytes;   // 期望字节（通配处为 0）
    std::vector<bool>    fixed;   // true = 该位必须精确匹配
    bool ok = false;
};

CompiledSig Compile(const char* sig) {
    CompiledSig out;
    if (!sig) return out;

    const char* p = sig;
    while (*p) {
        while (*p == ' ') ++p;
        if (!*p) break;

        if (p[0] == '?' && p[1] == '?') {
            out.bytes.push_back(0);
            out.fixed.push_back(false);
            p += 2;
        } else {
            auto hexval = [](char c) -> int {
                if (c >= '0' && c <= '9') return c - '0';
                if (c >= 'a' && c <= 'f') return c - 'a' + 10;
                if (c >= 'A' && c <= 'F') return c - 'A' + 10;
                return -1;
            };
            int hi = hexval(p[0]);
            int lo = hexval(p[1]);
            if (hi < 0 || lo < 0) return out;   // 解析失败
            out.bytes.push_back(static_cast<uint8_t>((hi << 4) | lo));
            out.fixed.push_back(true);
            p += 2;
        }
    }
    out.ok = !out.bytes.empty();
    return out;
}

// 在一段已读入的缓冲里找所有命中
int ScanBuffer(const CompiledSig& cs, const uint8_t* base, size_t len,
               void** firstHit) {
    const size_t n = cs.bytes.size();
    if (!cs.ok || n == 0 || len < n) return 0;

    const size_t limit = len - n;

    // 找一个固定字节做预筛锚点（取第一个固定位即可，命中率够用）
    size_t anchor = 0;
    bool foundAnchor = false;
    for (size_t i = 0; i < n; ++i) {
        if (cs.fixed[i]) { anchor = i; foundAnchor = true; break; }
    }

    int hits = 0;
    for (size_t i = 0; i <= limit; ++i) {
        if (foundAnchor && base[i + anchor] != cs.bytes[anchor]) continue;

        bool hit = true;
        for (size_t j = 0; j < n; ++j) {
            if (cs.fixed[j] && base[i + j] != cs.bytes[j]) { hit = false; break; }
        }
        if (hit) {
            if (hits == 0 && firstHit) *firstHit = const_cast<uint8_t*>(base + i);
            ++hits;
        }
    }
    return hits;
}

} // namespace

void* FindFirst(const char* sig, void* start, size_t len) {
    CompiledSig cs = Compile(sig);
    if (!cs.ok || !start || len == 0) return nullptr;
    void* first = nullptr;
    ScanBuffer(cs, static_cast<const uint8_t*>(start), len, &first);
    return first;
}

int CountAll(const char* sig, void* start, size_t len) {
    CompiledSig cs = Compile(sig);
    if (!cs.ok || !start || len == 0) return 0;
    return ScanBuffer(cs, static_cast<const uint8_t*>(start), len, nullptr);
}

// ---------------------------------------------------------------------------
// 逐页扫描整个主模块镜像
//
// 不改页面权限 —— 用 IsBadReadPtr 太慢，改成先 VirtualQuery 检查
// 确实是 committed 且 protection 允许读，然后直接 memcpy 到本地缓冲。
// 可执行段优先，因为特征码都是代码。
// ---------------------------------------------------------------------------
void* FindInImage(const char* sig, int* hitCount) {
    if (hitCount) *hitCount = 0;

    HMODULE mod = GetModuleHandleA(nullptr);
    if (!mod) return nullptr;

    MODULEINFO mi{};
    if (!GetModuleInformation(GetCurrentProcess(), mod, &mi, sizeof(mi)))
        return nullptr;

    CompiledSig cs = Compile(sig);
    if (!cs.ok) return nullptr;

    uint8_t* base = reinterpret_cast<uint8_t*>(mi.lpBaseOfDll);
    uint8_t* end  = base + mi.SizeOfImage;

    void* firstHit = nullptr;
    int   total    = 0;

    // 第一遍只扫可执行页，命中就够；没命中再全扫
    for (int pass = 0; pass < 2; ++pass) {
        uint8_t* p = base;
        while (p < end) {
            MEMORY_BASIC_INFORMATION mbi{};
            if (VirtualQuery(p, &mbi, sizeof(mbi)) == 0) break;

            uint8_t* regionBase = static_cast<uint8_t*>(mbi.BaseAddress);
            uint8_t* regionEnd  = regionBase + mbi.RegionSize;
            if (regionEnd > end) regionEnd = end;

            DWORD prot = mbi.Protect & 0xFF;
            bool readable = (mbi.State == MEM_COMMIT) &&
                            (prot == PAGE_READONLY ||
                             prot == PAGE_READWRITE ||
                             prot == PAGE_WRITECOPY ||
                             prot == PAGE_EXECUTE ||
                             prot == PAGE_EXECUTE_READ ||
                             prot == PAGE_EXECUTE_READWRITE ||
                             prot == PAGE_EXECUTE_WRITECOPY);
            bool exec = (prot == PAGE_EXECUTE ||
                         prot == PAGE_EXECUTE_READ ||
                         prot == PAGE_EXECUTE_READWRITE ||
                         prot == PAGE_EXECUTE_WRITECOPY);

            if (readable && (pass == 1 || exec)) {
                size_t sz = static_cast<size_t>(regionEnd - regionBase);
                std::vector<uint8_t> buf(sz);
                SIZE_T got = 0;
                if (ReadProcessMemory(GetCurrentProcess(), regionBase,
                                      buf.data(), sz, &got) && got == sz) {
                    void* hit = nullptr;
                    int n = ScanBuffer(cs, buf.data(), sz, &hit);
                    if (n > 0) {
                        if (!firstHit && hit)
                            firstHit = regionBase + (static_cast<uint8_t*>(hit) - buf.data());
                        total += n;
                    }
                }
            }
            p = regionEnd;
        }
        if (firstHit) break;   // 可执行页已经找到，不用再全扫
    }

    if (hitCount) *hitCount = total;
    return firstHit;
}

void* FindAppInstance() {
    // App 单例不再靠静态扫描 —— 钩子运行时能从 board 直接反查：
    //   0x00520AC0 的 edi 就是 Board*，[board] 就是 App*。
    // 见 tangshi.cpp 里的 g_board / g_app 缓存。
    return nullptr;
}

} // namespace Aob
