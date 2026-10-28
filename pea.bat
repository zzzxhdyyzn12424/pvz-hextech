@echo off
chcp 65001 >nul
pushd "%~dp0"
title PVZ 豌豆采集器 - 采集期间别关这个窗口

echo ============================================================
echo    PVZ 豌豆采集器
echo ============================================================
echo.
echo  1) 这个黑窗口开始跑之后，会自动把游戏窗口拉到最前面
echo  2) 你就正常玩：让豌豆射手真的开火（僵尸要走进射程）
echo  3) 采集 300 秒后自动结束，结果写进
echo     behavior\tools\build\pea_watch.json
echo.
echo  注意：采集期间不要点这个黑窗口，一点游戏就失焦暂停了
echo        要提前结束就按 Ctrl+C
echo.
pause
echo.
echo ---- 开始采集 ----
".venv\Scripts\python.exe" "behavior\tools\probe.py" --focus peawatch 300 "behavior\tools\build\pea_watch.json"
echo.
echo ---- 采集结束，结果在 behavior\tools\build\pea_watch.json ----
pause
popd
