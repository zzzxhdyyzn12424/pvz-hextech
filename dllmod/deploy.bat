@echo off
chcp 65001 >nul
setlocal
rem ===========================================================================
rem  deploy.bat  --  重新编译 + 部署到游戏目录
rem
rem  游戏正在跑的时候 DLL 是被锁住的，覆盖会失败（Device or resource busy）。
rem  所以这里先关游戏、再拷、再启动。会丢掉当前关卡的进度。
rem
rem  想自己手动来：关游戏 -> 拷 out\wsock32.dll -> 重新开游戏。
rem ===========================================================================

set ROOT=%~dp0
set GAME=F:\pvz\Plants_Vs_Zombies_V1.0.0.1051_CN_V2

call "%ROOT%build.bat"
if errorlevel 1 (
  echo [!] build failed
  exit /b 1
)

taskkill /F /IM PlantsVsZombies.exe >nul 2>&1
rem 等句柄释放，否则 copy 还是会 busy
ping -n 3 127.0.0.1 >nul

copy /Y "%ROOT%out\wsock32.dll"      "%GAME%\wsock32.dll"      >nul || goto :fail
copy /Y "%ROOT%out\wsock32_orig.dll" "%GAME%\wsock32_orig.dll" >nul || goto :fail
del /Q "%GAME%\hextech_dll.log" >nul 2>&1

echo [+] deployed to %GAME%
start "" "%GAME%\PlantsVsZombies.exe"
exit /b 0

:fail
echo [!] copy failed -- 游戏还占着 DLL，先把游戏完全关掉再跑一次
exit /b 1
