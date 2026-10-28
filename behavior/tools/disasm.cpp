// ===========================================================================
//  disasm.cpp —— 极简 x86 反汇编器（只为看清 PVZ 的浮点积分循环）
//
//  不追求完整：只认得 PVZ 里实际出现的这些指令形态。遇到不认识的字节就
//  打 "db" 然后前进 1 字节 —— 会错位，但对我们关心的浮点片段够用。
//
//  用法: disasm.exe <pid> <addr_hex> <len_hex>
// ===========================================================================
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <psapi.h>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <vector>
#include <string>

struct Region { uint32_t base; size_t size; std::vector<uint8_t> data; };

int main(int argc, char** argv) {
    if (argc < 4) { std::printf("usage: disasm.exe <pid> <addr_hex> <len_hex>\n"); return 1; }
    DWORD pid = static_cast<DWORD>(std::strtoul(argv[1], nullptr, 10));
    uint32_t addr = static_cast<uint32_t>(std::strtoul(argv[2], nullptr, 16));
    uint32_t len  = static_cast<uint32_t>(std::strtoul(argv[3], nullptr, 16));

    HANDLE h = OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, FALSE, pid);
    if (!h) { std::printf("OpenProcess failed: %lu\n", GetLastError()); return 1; }

    MEMORY_BASIC_INFORMATION mbi;
    SIZE_T got = VirtualQueryEx(h, reinterpret_cast<LPCVOID>(addr), &mbi, sizeof(mbi));
    if (!got) { std::printf("VirtualQueryEx failed\n"); CloseHandle(h); return 1; }

    uint32_t base = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(mbi.BaseAddress));
    size_t maxread = mbi.RegionSize - (addr - base);
    if (maxread > len) maxread = len;

    std::vector<uint8_t> buf(maxread + 16, 0xCC);
    SIZE_T read = 0;
    ReadProcessMemory(h, reinterpret_cast<LPCVOID>(addr), buf.data(), maxread, &read);
    CloseHandle(h);
    std::printf("read %zu bytes @0x%08X\n\n", read, addr);

    static const char* const R8[8] = {"eax","ecx","edx","ebx","esp","ebp","esi","edi"};
    static const char* const R16[8] = {"ax","cx","dx","bx","sp","bp","si","di"};

    uint32_t i = 0;
    while (i < read) {
        uint32_t a = addr + i;
        uint8_t op = buf[i];
        uint8_t b1 = buf[i+1];
        int n = 1;
        char txt[160] = {0};

        auto rm = [&](int modrm, int* out_disp, int* out_reg, int* out_kind) -> const char* {
            // 返回格式化的操作数，同时给出 reg 字段
            static char sbuf[80];
            int mod = (modrm >> 6) & 3;
            int reg = (modrm >> 3) & 7;
            int rmf = modrm & 7;
            *out_reg = reg;
            int d = 0, consumed = 0;
            const char* basereg = (rmf == 4) ? nullptr : R8[rmf];
            int sib_base = -1, sib_scale = 0, sib_index = -1;
            if (rmf == 4) {           // SIB 字节
                uint8_t sib = buf[i+3];
                sib_scale = 1 << ((sib >> 6) & 3);
                sib_index = (sib >> 3) & 7;
                sib_base  = sib & 7;
                consumed = 1;
                basereg = (sib_base == 5 && mod == 0) ? nullptr : R8[sib_base];
            }
            int disp_off = (rmf == 4) ? 3 : 2;
            int base_consumed = (rmf == 4) ? 1 : 0;
            if (mod == 0 && rmf == 5) { d = *reinterpret_cast<int32_t*>(&buf[i+disp_off]); consumed = base_consumed + 4; basereg = nullptr; }
            else if (mod == 1) { d = static_cast<int8_t>(buf[i+disp_off]); consumed = base_consumed + 1; }
            else if (mod == 2) { d = *reinterpret_cast<int32_t*>(&buf[i+disp_off]); consumed = base_consumed + 4; }
            else consumed = base_consumed;
            if (rmf == 4 && mod == 0 && sib_base == 5) { /* [index*scale+disp32] 已含 */ }
            *out_disp = d;
            *out_kind = mod;
            if (sib_base >= 0 || (rmf == 4)) {
                // [base + index*scale + disp]
                char inner[48];
                if (sib_index == 4) std::snprintf(inner, sizeof(inner), "%s", basereg ? basereg : "-");
                else if (basereg) std::snprintf(inner, sizeof(inner), "%s+%s*%d", basereg, R8[sib_index], sib_scale);
                else std::snprintf(inner, sizeof(inner), "%s*%d", R8[sib_index], sib_scale);
                if (d == 0) std::snprintf(sbuf, sizeof(sbuf), "[%s]", inner);
                else std::snprintf(sbuf, sizeof(sbuf), "[%s%+d]", inner, d);
                return sbuf;
            }
            if (!basereg) {
                std::snprintf(sbuf, sizeof(sbuf), "ds:0x%X", d);
            } else if (d == 0) std::snprintf(sbuf, sizeof(sbuf), "[%s]", basereg);
            else std::snprintf(sbuf, sizeof(sbuf), "[%s%+d]", basereg, d);
            return sbuf;
        };

        // ---- 浮点 ----
        if (op == 0xD9 || op == 0xD8 || op == 0xDD || op == 0xDC || op == 0xDE || op == 0xDA || op == 0xDB || op == 0xDF) {
            int mod = (b1 >> 6) & 3, ext = (b1 >> 3) & 7, rmf = b1 & 7;
            if (op == 0xDF && b1 == 0xE0) { std::snprintf(txt, sizeof(txt), "fnstsw ax"); n = 2; }
            else if (op == 0xDE && b1 == 0xD9) { std::snprintf(txt, sizeof(txt), "fcompp"); n = 2; }
            else if (mod == 3) {
                // 寄存器形态 D9 Ex / D8 Cx.. 
                if (op == 0xD9 && ext == 4) { std::snprintf(txt, sizeof(txt), "fld st(%d)", rmf); }
                else if (op == 0xD9 && ext == 5) { std::snprintf(txt, sizeof(txt), "fxch st(%d)", rmf); }
                else if (op == 0xD9 && rmf == 6 && ext == 6) { std::snprintf(txt, sizeof(txt), "fldz"); }
                else if (op == 0xD9 && rmf == 7 && ext == 6) { std::snprintf(txt, sizeof(txt), "fld1"); }
                else if (op == 0xD9 && rmf == 4 && ext == 6) { std::snprintf(txt, sizeof(txt), "fldpi"); }
                else if (op == 0xD8) {
                    if (ext == 0) std::snprintf(txt, sizeof(txt), "fadd st(0),st(%d)", rmf);
                    else if (ext == 1) std::snprintf(txt, sizeof(txt), "fmul st(0),st(%d)", rmf);
                    else if (ext == 4) std::snprintf(txt, sizeof(txt), "fsub st(0),st(%d)", rmf);
                    else if (ext == 6) std::snprintf(txt, sizeof(txt), "fdiv st(0),st(%d)", rmf);
                } else if (op == 0xDE && ext == 3) { std::snprintf(txt, sizeof(txt), "fcompp"); }
                else if (op == 0xD9 && ext == 7 && rmf == 1) { std::snprintf(txt, sizeof(txt), "fstp st(1)"); }
                else if (op == 0xDD && ext == 2) { std::snprintf(txt, sizeof(txt), "fst st(%d)", rmf); }
                else if (op == 0xDD && ext == 3) { std::snprintf(txt, sizeof(txt), "fstp st(%d)", rmf); }
                if (!txt[0]) {
                    std::snprintf(txt, sizeof(txt), "fpu? %02X %02X", op, b1);
                }
                n = 2;
            } else {
                int d, reg, kind;
                const char* mem = rm(b1, &d, &reg, &kind);
                int consumer = (mod == 1) ? 1 : (mod == 2 ? 4 : ((b1 & 7) == 5 ? 4 : 0));
                if ((b1 & 7) == 4) consumer += 1;   // SIB 字节
                const char* ty = (op == 0xDD || op == 0xDC) ? "qword" : "dword";
                const char* mn = "fpu?";
                if (op == 0xD9 && ext == 0) mn = "fld";
                else if (op == 0xD9 && ext == 2) mn = "fst";
                else if (op == 0xD9 && ext == 3) mn = "fstp";
                else if (op == 0xD9 && ext == 4) mn = "fldenv?";
                else if (op == 0xD9 && ext == 5) mn = "fldcw";
                else if (op == 0xD9 && ext == 7) mn = "fnstcw";
                else if (op == 0xD8 && ext == 0) mn = "fadd";
                else if (op == 0xD8 && ext == 1) mn = "fmul";
                else if (op == 0xD8 && ext == 4) mn = "fsub";
                else if (op == 0xD8 && ext == 5) mn = "fsubr";
                else if (op == 0xD8 && ext == 6) mn = "fdiv";
                else if (op == 0xD8 && ext == 7) mn = "fdivr";
                else if (op == 0xDD && ext == 0) mn = "fld";
                else if (op == 0xDD && ext == 2) mn = "fst";
                else if (op == 0xDD && ext == 3) mn = "fstp";
                else if (op == 0xDC && ext == 0) mn = "fadd";
                else if (op == 0xDC && ext == 1) mn = "fmul";
                else if (op == 0xDC && ext == 5) mn = "fsubr";
                else if (op == 0xDC && ext == 4) mn = "fsub";
                else if (op == 0xDC && ext == 6) mn = "fdiv";
                else if (op == 0xD8 && ext == 2) mn = "fcom";
                else if (op == 0xD8 && ext == 3) mn = "fcomp";
                std::snprintf(txt, sizeof(txt), "%s %s ptr %s", mn, ty, mem);
                n = 2 + consumer;
            }
        }
        // ---- push/pop ----
        else if (op >= 0x50 && op <= 0x57) { std::snprintf(txt, sizeof(txt), "push %s", R8[op - 0x50]); }
        else if (op >= 0x58 && op <= 0x5F) { std::snprintf(txt, sizeof(txt), "pop %s", R8[op - 0x58]); }
        else if (op >= 0xB8 && op <= 0xBF) {
            uint32_t imm = *reinterpret_cast<uint32_t*>(&buf[i+1]);
            std::snprintf(txt, sizeof(txt), "mov %s, 0x%X", R8[op - 0xB8], imm);
            n = 5;
        }
        else if (op == 0x6A) { std::snprintf(txt, sizeof(txt), "push %d", static_cast<int8_t>(b1)); n = 2; }
        else if (op == 0x68) {
            uint32_t imm = *reinterpret_cast<uint32_t*>(&buf[i+1]);
            std::snprintf(txt, sizeof(txt), "push 0x%X", imm);
            n = 5;
        }
        else if (op == 0xE8) {
            int32_t rel = *reinterpret_cast<int32_t*>(&buf[i+1]);
            std::snprintf(txt, sizeof(txt), "call 0x%08X", a + 5 + rel);
            n = 5;
        }
        else if (op == 0xE9) {
            int32_t rel = *reinterpret_cast<int32_t*>(&buf[i+1]);
            std::snprintf(txt, sizeof(txt), "jmp 0x%08X", a + 5 + rel);
            n = 5;
        }
        else if (op == 0xEB) { std::snprintf(txt, sizeof(txt), "jmp short 0x%08X", a + 2 + static_cast<int8_t>(b1)); n = 2; }
        else if (op >= 0x70 && op <= 0x7F) {
            static const char* const JCC[16] = {"jo","jno","jb","jae","je","jne","jbe","ja","js","jns","jp","jnp","jl","jge","jle","jg"};
            std::snprintf(txt, sizeof(txt), "%s short 0x%08X", JCC[op - 0x70], a + 2 + static_cast<int8_t>(b1));
            n = 2;
        }
        else if (op == 0x0F && b1 >= 0x80 && b1 <= 0x8F) {
            int32_t rel = *reinterpret_cast<int32_t*>(&buf[i+2]);
            static const char* const JCC[16] = {"jo","jno","jb","jae","je","jne","jbe","ja","js","jns","jp","jnp","jl","jge","jle","jg"};
            std::snprintf(txt, sizeof(txt), "%s near 0x%08X", JCC[b1 - 0x80], a + 6 + rel);
            n = 6;
        }
        else if (op == 0x83 || op == 0x81 || op == 0x80 || op == 0xFF) {
            int mod = (b1 >> 6) & 3, ext = (b1 >> 3) & 7, rmf = b1 & 7;
            int d, reg, kind;
            const char* mem = rm(b1, &d, &reg, &kind);
            int consumer = (mod == 1) ? 1 : (mod == 2 ? 4 : ((rmf == 5) ? 4 : 0));
            const char* mn = (op == 0xFF) ? "call/jmp" : ((op == 0x80) ? "cmpb" : "cmp/add/sub");
            if (op == 0xFF) {
                static const char* const FFN[8] = {"inc","dec","call","callf","jmp","jmpf","push","?"};
                mn = FFN[ext];
            } else if (op == 0x83) {
                static const char* const ALU[8] = {"add","or","adc","sbb","and","sub","xor","cmp"};
                mn = ALU[ext];
            }
            int64_t imm = 0;
            if (op == 0x83) imm = static_cast<int8_t>(buf[i+2+consumer]);
            else if (op == 0x81) imm = *reinterpret_cast<int32_t*>(&buf[i+2+consumer]);
            else if (op == 0x80) imm = static_cast<int8_t>(buf[i+2+consumer]);
            if (op == 0xFF) std::snprintf(txt, sizeof(txt), "%s %s", mn, mem);
            else std::snprintf(txt, sizeof(txt), "%s %s, %lld", mn, mem, static_cast<long long>(imm));
            n = 2 + consumer + ((op == 0x80) ? 1 : (op == 0x83 ? 1 : (op == 0x81 ? 4 : 0)));
        }
        // ---- 单字节立即数 ALU ----
        else if (op == 0x3C || op == 0x3D) {
            if (op == 0x3D) { std::snprintf(txt, sizeof(txt), "cmp eax, 0x%X", *reinterpret_cast<uint32_t*>(&buf[i+1])); n = 5; }
            else { std::snprintf(txt, sizeof(txt), "cmp al, 0x%X", b1); n = 2; }
        }
        else if (op == 0x0F && b1 == 0x9C) {
            static const char* const CC[16] = {"o","no","b","ae","e","ne","be","a","s","ns","p","np","l","ge","le","g"};
            int cc = buf[i+2] & 0x0F;
            int mod = (buf[i+3] >> 6) & 3, rmf = buf[i+3] & 7;
            int d, reg, kind;
            const char* mem = rm(buf[i+3], &d, &reg, &kind);
            int consumer = (mod == 1) ? 1 : (mod == 2 ? 4 : ((rmf == 5) ? 4 : 0));
            std::snprintf(txt, sizeof(txt), "set%s byte %s", CC[cc], mem);
            n = 4 + consumer;
        }
        // ---- 段前缀忽略 ----
        else if (op == 0x26 || op == 0x2E || op == 0x36 || op == 0x3E || op == 0x64 || op == 0x65 || op == 0x66 || op == 0x67 || op == 0xF2 || op == 0xF3) {
            std::snprintf(txt, sizeof(txt), "prefix 0x%02X", op);
        }
        else if (op == 0x8F) {
            std::snprintf(txt, sizeof(txt), "pop dword [esp]");
            n = 2;
        }
        else if (op == 0xD0 || op == 0xD2 || op == 0xD3) {
            std::snprintf(txt, sizeof(txt), "shift? 0x%02X 0x%02X", op, b1);
            n = 2;
        }
        else if (op == 0x8E || op == 0x8C || op == 0xA1 || op == 0xA3 || op == 0xA0 || op == 0xA2) {
            if (op == 0xA1 || op == 0xA3) {
                uint32_t imm = *reinterpret_cast<uint32_t*>(&buf[i+1]);
                std::snprintf(txt, sizeof(txt), "mov %s, moffs 0x%X", (op == 0xA1 ? "eax" : "moffs"), imm);
                n = 5;
            } else {
                std::snprintf(txt, sizeof(txt), "seg/mov 0x%02X 0x%02X", op, b1);
                n = 2;
            }
        }
        else if (op == 0x42 || op == 0x43 || op == 0x41 || op == 0x40 || op == 0x49 || op == 0x48 || op == 0x4A || op == 0x4B) {
            static const char* const NM[8] = {"inc eax","inc ecx","inc edx","inc ebx","dec eax","dec ecx","dec edx","dec ebx"};
            std::snprintf(txt, sizeof(txt), "%s", NM[op - 0x40]);
        }
        else if (op == 0x3D) { }  // handled above
        else if (op == 0x24 || op == 0x25) {
            if (op == 0x25) { std::snprintf(txt, sizeof(txt), "and eax, 0x%X", *reinterpret_cast<uint32_t*>(&buf[i+1])); n = 5; }
            else { std::snprintf(txt, sizeof(txt), "and al, 0x%X", b1); n = 2; }
        }
        else if (op == 0x74 || op == 0x75) { /* handled by 0x70..0x7F */ }
        else if (op == 0xA9) { std::snprintf(txt, sizeof(txt), "test eax, 0x%X", *reinterpret_cast<uint32_t*>(&buf[i+1])); n = 5; }
        else if (op == 0x84 && ((b1 >> 6) & 3) == 3) {
            std::snprintf(txt, sizeof(txt), "test %s, %s", R8[b1 & 7], R8[(b1 >> 3) & 7]);
            n = 2;
        }
        else if (op == 0x85 && ((b1 >> 6) & 3) == 3) {
            std::snprintf(txt, sizeof(txt), "test %s, %s", R8[b1 & 7], R8[(b1 >> 3) & 7]);
            n = 2;
        }
        else if (op == 0x39 && ((b1 >> 6) & 3) == 3) {
            std::snprintf(txt, sizeof(txt), "cmp %s, %s", R8[b1 & 7], R8[(b1 >> 3) & 7]);
            n = 2;
        }
        else if (op == 0x31 || op == 0x29 || op == 0x01 || op == 0x21) {
            static const char* const AN[4] = {"xor","sub","add","and"};
            int idx = (op == 0x31) ? 0 : (op == 0x29 ? 1 : (op == 0x01 ? 2 : 3));
            if (((b1 >> 6) & 3) == 3) {
                std::snprintf(txt, sizeof(txt), "%s %s, %s", AN[idx], R8[b1 & 7], R8[(b1 >> 3) & 7]);
                n = 2;
            } else {
                int d, reg, kind;
                const char* mem = rm(b1, &d, &reg, &kind);
                int consumer = (((b1 >> 6) & 3) == 1) ? 1 : (((b1 >> 6) & 3) == 2 ? 4 : (((b1 & 7) == 5) ? 4 : 0));
                std::snprintf(txt, sizeof(txt), "%s %s, %s", AN[idx], mem, R8[(b1 >> 3) & 7]);
                n = 2 + consumer;
            }
        }
        else if (op == 0x8B || op == 0x89 || op == 0x8D || op == 0x3B || op == 0x03 || op == 0x2B || op == 0x33) {
            int mod = (b1 >> 6) & 3, ext = (b1 >> 3) & 7, rmf = b1 & 7;
            if (mod == 3) {
                const char* mn = "?";
                bool regdst = false;
                switch (op) {
                    case 0x8B: mn = "mov"; regrdst: regdst = true; break;
                    case 0x89: mn = "mov"; break;
                    case 0x8D: mn = "lea"; regdst = true; break;
                    case 0x3B: mn = "cmp"; regdst = true; break;
                    case 0x03: mn = "add"; regdst = true; break;
                    case 0x2B: mn = "sub"; regdst = true; break;
                    case 0x33: mn = "xor"; regdst = true; break;
                }
                if (regdst) std::snprintf(txt, sizeof(txt), "%s %s, %s", mn, R8[ext], R8[rmf]);
                else std::snprintf(txt, sizeof(txt), "%s %s, %s", mn, R8[rmf], R8[ext]);
                n = 2;
            } else {
                int d, reg, kind;
                const char* mem = rm(b1, &d, &reg, &kind);
                int consumer = (mod == 1) ? 1 : (mod == 2 ? 4 : ((rmf == 5) ? 4 : 0));
                const char* mn = "?";
                bool regdst = false;
                switch (op) {
                    case 0x8B: mn = "mov"; regdst = true; break;
                    case 0x89: mn = "mov"; break;
                    case 0x8D: mn = "lea"; regdst = true; break;
                    case 0x3B: mn = "cmp"; regdst = true; break;
                    case 0x03: mn = "add"; regdst = true; break;
                    case 0x2B: mn = "sub"; regdst = true; break;
                    case 0x33: mn = "xor"; regdst = true; break;
                }
                if (regdst) std::snprintf(txt, sizeof(txt), "%s %s, %s", mn, R8[ext], mem);
                else std::snprintf(txt, sizeof(txt), "%s %s, %s", mn, mem, R8[ext]);
                n = 2 + consumer;
            }
        }
        else if (op == 0x55) std::snprintf(txt, sizeof(txt), "push ebp");
        else if (op == 0xC7) {
            int mod = (b1 >> 6) & 3, rmf = b1 & 7;
            int d, reg, kind;
            const char* mem = rm(b1, &d, &reg, &kind);
            int consumer = (mod == 1) ? 1 : (mod == 2 ? 4 : ((rmf == 5) ? 4 : 0));
            uint32_t imm = *reinterpret_cast<uint32_t*>(&buf[i+2+consumer]);
            std::snprintf(txt, sizeof(txt), "mov dword %s, 0x%X", mem, imm);
            n = 2 + consumer + 4;
        }
        else if (op == 0x8B || op == 0x89 || op == 0x8D || op == 0x39 || op == 0x3B || op == 0x85 || op == 0x03 || op == 0x2B || op == 0x33) {
            int mod = (b1 >> 6) & 3, ext = (b1 >> 3) & 7, rmf = b1 & 7;
            int d, reg, kind;
            const char* mem = rm(b1, &d, &reg, &kind);
            int consumer = (mod == 1) ? 1 : (mod == 2 ? 4 : ((rmf == 5) ? 4 : 0));
            const char* mn = "?";
            bool regdst = false;
            switch (op) {
                case 0x8B: mn = "mov"; regdst = true; break;
                case 0x89: mn = "mov"; break;
                case 0x8D: mn = "lea"; regdst = true; break;
                case 0x39: mn = "cmp"; break;
                case 0x3B: mn = "cmp"; regdst = true; break;
                case 0x85: mn = "test"; break;
                case 0x03: mn = "add"; regdst = true; break;
                case 0x2B: mn = "sub"; regdst = true; break;
                case 0x33: mn = "xor"; regdst = true; break;
            }
            if (regdst) std::snprintf(txt, sizeof(txt), "%s %s, %s", mn, R8[ext], mem);
            else std::snprintf(txt, sizeof(txt), "%s %s, %s", mn, mem, R8[ext]);
            n = 2 + consumer;
        }
        else if (op == 0x55) std::snprintf(txt, sizeof(txt), "push ebp");
        else if (op == 0x5D) std::snprintf(txt, sizeof(txt), "pop ebp");
        else if (op == 0x56) std::snprintf(txt, sizeof(txt), "push esi");
        else if (op == 0x57) std::snprintf(txt, sizeof(txt), "push edi");
        else if (op == 0x5E) std::snprintf(txt, sizeof(txt), "pop esi");
        else if (op == 0x5F) std::snprintf(txt, sizeof(txt), "pop edi");
        else if (op == 0x53) std::snprintf(txt, sizeof(txt), "push ebx");
        else if (op == 0x5B) std::snprintf(txt, sizeof(txt), "pop ebx");
        else if (op == 0x90) std::snprintf(txt, sizeof(txt), "nop");
        else if (op == 0xC3) std::snprintf(txt, sizeof(txt), "ret");
        else if (op == 0xC2) { std::snprintf(txt, sizeof(txt), "ret %u", *reinterpret_cast<uint16_t*>(&buf[i+1])); n = 3; }
        else if (op == 0xCC) std::snprintf(txt, sizeof(txt), "int3");
        else if (op == 0xC9) std::snprintf(txt, sizeof(txt), "leave");
        else if (op == 0xF7 || op == 0xF6) {
            int mod = (b1 >> 6) & 3, ext = (b1 >> 3) & 7, rmf = b1 & 7;
            int d, reg, kind;
            const char* mem = rm(b1, &d, &reg, &kind);
            int consumer = (mod == 1) ? 1 : (mod == 2 ? 4 : ((rmf == 5) ? 4 : 0));
            static const char* const F7N[8] = {"test","test","not","neg","mul","imul","div","idiv"};
            std::snprintf(txt, sizeof(txt), "%s %s", F7N[ext], mem);
            n = 2 + consumer;
        }
        else if (op == 0x8A || op == 0x88 || op == 0x84 || op == 0x38 || op == 0x3A) {
            int mod = (b1 >> 6) & 3, ext = (b1 >> 3) & 7, rmf = b1 & 7;
            int d, reg, kind;
            const char* mem = rm(b1, &d, &reg, &kind);
            int consumer = (mod == 1) ? 1 : (mod == 2 ? 4 : ((rmf == 5) ? 4 : 0));
            const char* mn = (op == 0x8A) ? "movzx8" : (op == 0x88 ? "mov8" : "test8");
            std::snprintf(txt, sizeof(txt), "%s %s, %s", mn, mem, R8[ext]);
            n = 2 + consumer;
        }
        else if (op == 0xC1 || op == 0xD1) {
            int mod = (b1 >> 6) & 3, ext = (b1 >> 3) & 7, rmf = b1 & 7;
            int d, reg, kind;
            const char* mem = rm(b1, &d, &reg, &kind);
            int consumer = (mod == 1) ? 1 : (mod == 2 ? 4 : ((rmf == 5) ? 4 : 0));
            static const char* const SHN[8] = {"rol","ror","rcl","rcr","shl","shr","shl","sar"};
            if (op == 0xD1) std::snprintf(txt, sizeof(txt), "%s %s, 1", SHN[ext], mem);
            else {
                uint8_t imm = buf[i+2+consumer];
                std::snprintf(txt, sizeof(txt), "%s %s, %u", SHN[ext], mem, imm);
                consumer += 1;
            }
            n = 2 + consumer;
        }
        else if (op == 0x0F && b1 == 0xB6) {
            int mod = (buf[i+2] >> 6) & 3, ext = (buf[i+2] >> 3) & 7, rmf = buf[i+2] & 7;
            int d, reg, kind;
            const char* mem = rm(buf[i+2], &d, &reg, &kind);
            int consumer = (mod == 1) ? 1 : (mod == 2 ? 4 : ((rmf == 5) ? 4 : 0));
            std::snprintf(txt, sizeof(txt), "movzx %s, byte %s", R8[ext], mem);
            n = 3 + consumer;
        }
        else {
            std::snprintf(txt, sizeof(txt), "db 0x%02X", op);
        }

        std::printf("%08X  ", a);
        for (int k = 0; k < n && k < 8; ++k) std::printf("%02X ", buf[i + k]);
        for (int k = n; k < 8; ++k) std::printf("   ");
        std::printf(" %s\n", txt);
        i += n;
    }
    return 0;
}
