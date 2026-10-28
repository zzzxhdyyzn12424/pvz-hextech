// ============================================================================
//  inject.cpp  ——  唐诗射手 DLL 注入器
//
//  用法：
//    tangshi_inject.exe                        # 自动找游戏，注入 tangshi.dll
//    tangshi_inject.exe D:\pvz\PlantsVsZombies.exe
//
//  流程：CreateProcess(SUSPENDED) -> VirtualAllocEx 写路径 ->
//        CreateRemoteThread(LoadLibraryA) -> ResumeThread
// ============================================================================

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <tlhelp32.h>
#include <cstdio>
#include <cstring>
#include <string>

static void Pause(const char* msg = "\n按回车退出...") {
    printf("%s", msg);
    (void)getchar();
}

// 关掉 ASLR 对特征码扫描的影响？不需要 —— 我们扫的是模块内部相对偏移，
// 基址变化不影响机器码内容。开着反而更简单。

static DWORD FindProcessByName(const char* name) {
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return 0;

    PROCESSENTRY32 pe{};
    pe.dwSize = sizeof(pe);
    DWORD pid = 0;

    if (Process32First(snap, &pe)) {
        do {
            if (_stricmp(pe.szExeFile, name) == 0) { pid = pe.th32ProcessID; break; }
        } while (Process32Next(snap, &pe));
    }
    CloseHandle(snap);
    return pid;
}

static bool InjectDll(DWORD pid, const char* dllPath) {
    HANDLE proc = OpenProcess(
        PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION |
        PROCESS_VM_OPERATION | PROCESS_VM_WRITE | PROCESS_VM_READ,
        FALSE, pid);
    if (!proc) {
        printf("[!] OpenProcess 失败，错误码 %lu\n", GetLastError());
        return false;
    }

    size_t len = strlen(dllPath) + 1;
    void* remote = VirtualAllocEx(proc, nullptr, len,
                                  MEM_COMMIT | MEM_RESERVE,
                                  PAGE_READWRITE);
    if (!remote) {
        printf("[!] VirtualAllocEx 失败，错误码 %lu\n", GetLastError());
        CloseHandle(proc);
        return false;
    }

    if (!WriteProcessMemory(proc, remote, dllPath, len, nullptr)) {
        printf("[!] WriteProcessMemory 失败，错误码 %lu\n", GetLastError());
        VirtualFreeEx(proc, remote, 0, MEM_RELEASE);
        CloseHandle(proc);
        return false;
    }

    HMODULE k32 = GetModuleHandleA("kernel32.dll");
    FARPROC loadLib = GetProcAddress(k32, "LoadLibraryA");
    if (!loadLib) {
        printf("[!] 找不到 LoadLibraryA\n");
        CloseHandle(proc);
        return false;
    }

    // LoadLibraryA 的签名是 DWORD WINAPI(LPCSTR)，跟 LPTHREAD_START_ROUTINE
    // 的 DWORD WINAPI(LPVOID) 只差参数类型，实际 ABI 完全一致。
    // 必须过一层 void*，否则 GCC 报 -Wcast-function-type。
    LPTHREAD_START_ROUTINE start =
        reinterpret_cast<LPTHREAD_START_ROUTINE>(reinterpret_cast<void*>(loadLib));

    HANDLE th = CreateRemoteThread(proc, nullptr, 0, start, remote, 0, nullptr);
    if (!th) {
        printf("[!] CreateRemoteThread 失败，错误码 %lu\n", GetLastError());
        VirtualFreeEx(proc, remote, 0, MEM_RELEASE);
        CloseHandle(proc);
        return false;
    }

    WaitForSingleObject(th, 10000);
    DWORD exitCode = 0;
    GetExitCodeThread(th, &exitCode);
    // exitCode 就是 LoadLibraryA 的返回值（远程模块基址）
    printf("[+] LoadLibraryA 返回 0x%08lX %s\n", exitCode,
           exitCode ? "(成功)" : "(失败，看游戏目录里 tangshi.dll 是否存在)");

    CloseHandle(th);
    VirtualFreeEx(proc, remote, 0, MEM_RELEASE);
    CloseHandle(proc);
    return exitCode != 0;
}

static std::string ExeDir() {
    char buf[MAX_PATH];
    GetModuleFileNameA(nullptr, buf, MAX_PATH);
    char* slash = strrchr(buf, '\\');
    if (slash) *slash = 0;
    return std::string(buf);
}

int main(int argc, char** argv) {
    printf("=== 唐诗射手 行为注入器 ===\n\n");

    std::string dir = ExeDir();
    std::string dll = dir + "\\tangshi.dll";

    if (GetFileAttributesA(dll.c_str()) == INVALID_FILE_ATTRIBUTES) {
        printf("[!] 找不到 %s\n", dll.c_str());
        printf("    请先把 tangshi.dll 放到注入器同目录。\n");
        Pause();
        return 1;
    }
    printf("[+] DLL: %s\n", dll.c_str());

    const char* exeName = "PlantsVsZombies.exe";
    DWORD pid = FindProcessByName(exeName);

    if (pid) {
        printf("[+] 找到已运行的游戏，PID=%lu\n", pid);
    } else {
        printf("[*] 游戏没在运行，尝试启动...\n");

        std::string exePath;
        if (argc > 1) {
            exePath = argv[1];
        } else {
            printf("[!] 请把游戏路径作为参数传入，例如：\n");
            printf("    tangshi_inject.exe \"F:\\pvz\\PlantsVsZombies.exe\"\n");
            Pause();
            return 1;
        }
        if (GetFileAttributesA(exePath.c_str()) == INVALID_FILE_ATTRIBUTES) {
            printf("[!] 路径不存在: %s\n", exePath.c_str());
            Pause();
            return 1;
        }

        STARTUPINFOA si{};
        si.cb = sizeof(si);
        PROCESS_INFORMATION pi{};

        // 注意：不能用 CREATE_SUSPENDED 后再 Resume ——
        // PVZ 会在启动时自己校验/初始化，挂起太久容易出问题。
        // 直接启动，等窗口出现再注入。
        std::string workDir = exePath.substr(0, exePath.find_last_of('\\'));
        if (!CreateProcessA(exePath.c_str(), nullptr, nullptr, nullptr,
                            FALSE, 0, nullptr, workDir.c_str(), &si, &pi)) {
            printf("[!] CreateProcess 失败，错误码 %lu\n", GetLastError());
            Pause();
            return 1;
        }
        pid = pi.dwProcessId;
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
        printf("[+] 已启动，PID=%lu，等待加载...\n", pid);

        // 等窗口
        for (int i = 0; i < 200; ++i) {
            HWND w = FindWindowA(nullptr, nullptr);
            (void)w;
            Sleep(100);
            if (FindProcessByName(exeName)) break;
        }
        Sleep(3000);   // 让游戏把 pak 读完
    }

    if (!InjectDll(pid, dll.c_str())) {
        printf("[!] 注入失败\n");
        Pause();
        return 1;
    }

    printf("\n[+] 注入完成。游戏里种下豌豆射手试试：\n");
    printf("    - 10 发里应有约 4 发歪着飞出去\n");
    printf("    - 连歪 10 发会变红、密集吐豆、然后爆炸\n");
    printf("    - 爆炸后全场僵尸掉头逃跑 3 秒\n");
    printf("\n如果没反应，看游戏目录下有没有 tangshi.log。\n");
    Pause();
    return 0;
}
