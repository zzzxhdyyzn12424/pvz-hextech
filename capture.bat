@echo off
chcp 65001 >nul
setlocal
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

echo ============================================================
echo  PVZ pea capture - READ THIS FIRST
echo ============================================================
echo.
echo  1. This window will start sampling for 180 seconds.
echo  2. Click back to the game window (the game pauses when it
echo     loses focus, so do NOT leave the game in the background).
echo  3. Enter 1-1, plant a Peashooter, and let it shoot
echo     at least 3-4 rounds (a zombie must be in the SAME lane).
echo  4. When the countdown finishes, come back here.
echo.
echo  This is READ-ONLY. It never writes to game memory.
echo ============================================================
echo.
pause

".venv\Scripts\python.exe" behavior\tools\probe.py capture 180 build\pea_capture.json

echo.
echo ============================================================
echo  Done. Result: build\pea_capture.json
echo ============================================================
pause
