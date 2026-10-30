// ============================================================================
//  log.h —— 进程内日志
//
//  游戏进程里没法 printf 给人看，只能落盘。路径固定为 exe 同目录的
//  hextech_dll.log，每次写都重新 Open/Close，这样用户随时能 tail。
// ============================================================================
#pragma once

#include <windows.h>
#include <cstdarg>
#include <cstdio>
#include <cstring>

namespace hx {

inline char* LogPath() {
    static char s_path[MAX_PATH] = {0};
    if (!s_path[0]) {
        GetModuleFileNameA(nullptr, s_path, MAX_PATH);
        char* slash = strrchr(s_path, '\\');
        if (slash) *(slash + 1) = 0;
        strncat(s_path, "hextech_dll.log", MAX_PATH - strlen(s_path) - 1);
    }
    return s_path;
}

inline void LogReset() {
    HANDLE h = CreateFileA(LogPath(), GENERIC_WRITE,
                           FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                           CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h != INVALID_HANDLE_VALUE) CloseHandle(h);
}

inline void Log(const char* fmt, ...) {
    char line[1024];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(line, sizeof(line) - 2, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof(line) - 2) n = (int)sizeof(line) - 2;
    line[n++] = '\r';
    line[n++] = '\n';

    HANDLE h = CreateFileA(LogPath(), FILE_APPEND_DATA,
                           FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                           OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD wrote = 0;
    WriteFile(h, line, (DWORD)n, &wrote, nullptr);
    CloseHandle(h);
}

}  // namespace hx
