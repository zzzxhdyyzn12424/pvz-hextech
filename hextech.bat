@echo off
chcp 65001 >nul
cd /d "%~dp0"
title PVZ 海克斯面板
".venv\Scripts\python.exe" tools\hextech.py panel
if errorlevel 1 pause
