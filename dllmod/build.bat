@echo off
chcp 65001 >nul
setlocal

rem ===========================================================================
rem  build.bat  --  build the wsock32.dll proxy for the Hextech mod
rem
rem  Produces (in dllmod\out\):
rem     wsock32.dll        our proxy, forwards all 75 exports to wsock32_orig
rem     wsock32_orig.dll   a copy of the real C:\Windows\SysWOW64\wsock32.dll
rem
rem  Deploy: copy BOTH files into the PVZ game folder, next to
rem  PlantsVsZombies.exe. Launch the game normally. That's it.
rem
rem  NOTE: this file must stay ASCII + CRLF. cmd.exe mangles UTF-8 comments.
rem ===========================================================================

set ROOT=%~dp0
set SRC=%ROOT%src
set GEN=%ROOT%gen
set OUT=%ROOT%out
set TCBIN=C:\pvztc\mingw32\bin
set PY=%ROOT%..\.venv\Scripts\python.exe

if not exist "%TCBIN%\i686-w64-mingw32-g++.exe" (
  echo [!] compiler not found: %TCBIN%\i686-w64-mingw32-g++.exe
  exit /b 1
)
if not exist "%PY%" (
  echo [!] python not found: %PY%
  exit /b 1
)

if not exist "%GEN%" mkdir "%GEN%"
if not exist "%OUT%" mkdir "%OUT%"

set PATH=%TCBIN%;%PATH%
set CXX=%TCBIN%\i686-w64-mingw32-g++.exe
set CFLAGS=-m32 -std=c++17 -O2 -Wall -Wextra -Wno-unused-parameter -fno-use-linker-plugin -static-libgcc -static-libstdc++ -D_CRT_SECURE_NO_WARNINGS

echo [1/3] generating export forwarders ...
"%PY%" "%ROOT%gen_proxy.py"
if errorlevel 1 exit /b 1

echo [2/3] copying real wsock32.dll ...
copy /Y "C:\Windows\SysWOW64\wsock32.dll" "%OUT%\wsock32_orig.dll" >nul
if errorlevel 1 (
  echo [!] copy failed
  exit /b 1
)

echo [3/3] compiling wsock32.dll ...
"%CXX%" %CFLAGS% -I"%SRC%" -shared ^
  "%SRC%\proxy.cpp" "%GEN%\proxy.def" ^
  -o "%OUT%\wsock32.dll" ^
  -static -luser32
if errorlevel 1 (
  echo [!] build failed
  exit /b 1
)

echo.
echo [+] done:
dir /b "%OUT%"
echo.
echo Deploy: copy out\wsock32.dll and out\wsock32_orig.dll into the game folder.
