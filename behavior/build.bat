@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

rem ===========================================================================
rem  build.bat  ——  编译唐诗射手行为模块 (32-bit DLL + 注入器)
rem
rem  编译器：C:\pvztc\mingw32\bin\i686-w64-mingw32-g++.exe (GCC 16.2.0 / MSYS2)
rem  安装： python behavior\fetch_toolchain.py  ->  behavior\toolchain\mingw32
rem          再  xcopy /E /I /Y behavior\toolchain\mingw32 C:\pvztc\mingw32
rem
rem  ⚠ 为什么工具链必须放 C:\pvztc 这种纯 ASCII 路径：
rem     本仓库路径含中文「默认项目」。GCC 的 ld.exe 用 ANSI 拼路径，
rem     遇到中文变成 C:/Users/33854/Documents/Ĭ����Ŀ/... ，
rem     然后既找不到 crt2.o 也没法写出输出文件。
rem     编译在本目录做（源文件用 -I 绝对路径传入没问题），
rem     但 **链接产物必须写到 ASCII 路径**，然后再复制回来。
rem     （HANDOFF.md §9 第 8 条早就记过这个坑，这次真的撞上了）
rem
rem  产物：behavior\out\tangshi.dll / tangshi_inject.exe
rem  本文件必须 ASCII + CRLF。
rem ===========================================================================

set ROOT=%~dp0
set SRC=%ROOT%src
set OUT=%ROOT%out
set TCBIN=C:\pvztc\mingw32\bin
set STAGE=C:\pvztc\out

if not exist "%OUT%" mkdir "%OUT%"
if not exist "%STAGE%" mkdir "%STAGE%"

if not exist "%TCBIN%\i686-w64-mingw32-g++.exe" (
  echo [!] 找不到编译器 %TCBIN%\i686-w64-mingw32-g++.exe
  echo.
  echo     安装步骤：
  echo       1^) python behavior\fetch_toolchain.py
  echo       2^) xcopy /E /I /Y behavior\toolchain\mingw32 C:\pvztc\mingw32
  pause
  exit /b 1
)

set PATH=%TCBIN%;%PATH%
set CXX=%TCBIN%\i686-w64-mingw32-g++.exe

rem -m32                32 位目标（PVZ 是 i386）
rem -fno-use-linker-plugin  这台机器没有 lld 插件
rem -static-libgcc / -static-libstdc++  不依赖外部 GCC 运行库
set CFLAGS=-m32 -std=c++17 -O2 -Wall -Wextra -Wno-unused-parameter ^
 -fno-use-linker-plugin -static-libgcc -static-libstdc++ -D_CRT_SECURE_NO_WARNINGS

echo [*] 编译器: %CXX%
echo [*] 输出中转: %STAGE%  (ASCII 路径，绕开 ld.exe 的 ANSI 路径问题)
echo.

rem ================================================================ DLL
echo [1/3] 编译 tangshi.dll ...
"%CXX%" %CFLAGS% -I"%SRC%" -shared ^
  "%SRC%\tangshi.cpp" "%SRC%\aob.cpp" "%SRC%\ballistic.cpp" "%SRC%\aim.cpp" ^
  -o "%STAGE%\tangshi.dll" ^
  -static -Wl,--kill-at -luser32 -lpsapi
if errorlevel 1 (
  echo [!] tangshi.dll 编译失败
  pause
  exit /b 1
)
copy /Y "%STAGE%\tangshi.dll" "%OUT%\tangshi.dll" >nul
echo     OK  %OUT%\tangshi.dll

rem ================================================================ 自测
echo [2/3] 抛物线数学离线自测 ...
"%CXX%" %CFLAGS% -Wno-unused-function -Wno-comment -I"%SRC%" ^
  "%SRC%\test_ballistic.cpp" "%SRC%\ballistic.cpp" "%SRC%\aim.cpp" ^
  -o "%STAGE%\test_ballistic.exe" -static
if errorlevel 1 (
  echo [!] 自测编译失败
  pause
  exit /b 1
)
"%STAGE%\test_ballistic.exe"
if errorlevel 1 (
  echo.
  echo [!] 自测没过。物理/落点数学有问题，先修这里，别去动游戏。
  pause
  exit /b 1
)

rem ---------------------------------------------------------------- 注入器
echo [3/3] 编译 tangshi_inject.exe ...
"%CXX%" %CFLAGS% ^
  "%ROOT%inject\inject.cpp" ^
  -o "%STAGE%\tangshi_inject.exe" ^
  -static -luser32
if errorlevel 1 (
  echo [!] 注入器编译失败
  pause
  exit /b 1
)
copy /Y "%STAGE%\tangshi_inject.exe" "%OUT%\tangshi_inject.exe" >nul
echo     OK  %OUT%\tangshi_inject.exe

echo.
echo [+] 完成。
echo.
echo [!] 重要：aob_patterns.h 里四条特征码还是空的。
echo     现在这个 DLL 注入后会弹窗报"特征码扫描失败"然后安全退出，
echo     游戏不会坏，但也完全不会有任何效果。
echo     必须先按 aob_patterns.h 顶部的说明真机提取特征码。
echo.
pause
