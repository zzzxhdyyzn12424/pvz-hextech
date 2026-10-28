@echo off
chcp 65001 >nul
setlocal
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto setup
goto run

:setup
echo [*] Creating virtual environment...
python -m venv .venv
if errorlevel 1 goto err
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto err

:run
if "%~1"=="" goto usage
".venv\Scripts\python.exe" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%

:usage
echo.
echo Usage:
echo   run.bat tools\scan_textures.py --unpacked work\unpacked
echo   run.bat tools\prepare_textures.py --config config.json --preview
echo   run.bat tools\verify_textures.py --config config.json --sheet build\verify.png
echo   run.bat tools\build_mod.py --config config.json
echo.
pause
exit /b 0

:err
echo [x] Setup failed. Install Python 3.10+ and make sure python --version works.
pause
exit /b 1
