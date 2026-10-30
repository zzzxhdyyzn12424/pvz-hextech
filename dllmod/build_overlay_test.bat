@echo off
chcp 65001 >nul
setlocal
rem ===========================================================================
rem  build_overlay_test.bat -- offline render self-check (no game needed)
rem
rem  Produces out\overlay_test.exe. Run it, then look at C:\pvztc\overlay_test.bmp
rem  to verify HP bars / hex cards / detail panel layout and Chinese text.
rem
rem  NOTE: this file must stay ASCII + CRLF. cmd.exe mangles UTF-8 comments.
rem ===========================================================================
set ROOT=%~dp0
set SRC=%ROOT%src
set GEN=%ROOT%gen
set OUT=%ROOT%out
set TCBIN=C:\pvztc\mingw32\bin

if not exist "%TCBIN%\i686-w64-mingw32-g++.exe" (
  echo [!] compiler not found
  exit /b 1
)

set PATH=%TCBIN%;%PATH%
set CXX=%TCBIN%\i686-w64-mingw32-g++.exe

"%CXX%" -m32 -std=c++17 -O1 -Wall -fno-use-linker-plugin -static-libgcc -static-libstdc++ ^
  -I"%ROOT%." -I"%SRC%" -I"%GEN%" ^
  "%ROOT%test\overlay_test.cpp" ^
  -o "%OUT%\overlay_test.exe" -static -luser32 -lgdi32
if errorlevel 1 (
  echo [!] build failed
  exit /b 1
)
echo [+] built %OUT%\overlay_test.exe
