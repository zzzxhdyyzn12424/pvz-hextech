@echo off
chcp 65001 >nul
cd /d "%~dp0"
title PVZ 海克斯 · 游戏内显示
echo.
echo   正在启动游戏内覆盖层……
echo   游戏窗口请保持「非最小化」，否则覆盖层会暂时隐藏。
echo.
echo   F7   血量显示  开/关   （照抄杂交版：植物数字、僵尸三色血条）
echo   F8   详情面板   开/关
echo   F9   效果结算   暂停/恢复
echo   F10  重新抽词条
echo.
echo   关掉这个黑窗口 = 退出覆盖层
echo.
".venv\Scripts\python.exe" tools\hextech.py run
if errorlevel 1 pause
