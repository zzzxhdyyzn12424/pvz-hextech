@echo off
rem ===================================================================
rem   PVZ Hextech mod - installer
rem
rem   Usage (either way):
rem     1) drag the GAME FOLDER onto this .bat
rem     2) or double-click it and paste the game folder path
rem
rem   It does two things:
rem     - copies wsock32.dll (the mod) into the game folder
rem     - if wsock32_orig.dll is missing, copies it from the system dir
rem       (the proxy forwards to the real one; both files must exist)
rem
rem   NOTE: keep this file ASCII-only + CRLF. Non-ASCII text makes cmd
rem   resume reading at a wrong byte offset after chcp and execute
rem   fragments of lines as commands.
rem ===================================================================

set "SRC=%~dp0"
set "GAME=%~1"

if "%GAME%"=="" goto ask
if /i "%~x1"==".exe" set "GAME=%~dp1"
goto check

:ask
if exist "%SRC%PlantsVsZombies.exe" set "GAME=%SRC%"& goto check
echo.
echo No game folder given.
echo Drag the game folder onto this file, or paste the path below.
echo Example: F:/pvz/Plants_Vs_Zombies_V1.0.0.1051_CN_V2
echo.
set /p "GAME=Game folder: "

:check
set "GAME=%GAME:"=%"
if "%GAME%"=="" goto ask

if not exist "%GAME%\PlantsVsZombies.exe" goto noexe
if not exist "%SRC%wsock32.dll" goto nodll

echo.
echo Target: %GAME%
echo.

rem ---- 1/3 back up an existing wsock32.dll ----
if not exist "%GAME%\wsock32.dll" goto noold
if exist "%GAME%\wsock32.dll.bak" goto hadbak
copy /Y "%GAME%\wsock32.dll" "%GAME%\wsock32.dll.bak" >nul
echo [1/3] old wsock32.dll backed up to wsock32.dll.bak
goto orig
:hadbak
echo [1/3] old wsock32.dll and its backup both exist, skipped
goto orig
:noold
echo [1/3] no existing wsock32.dll, nothing to back up

rem ---- 2/3 make sure wsock32_orig.dll exists ----
:orig
if exist "%GAME%\wsock32_orig.dll" goto origok
set "ORIG=%SystemRoot%\SysWOW64\wsock32.dll"
if not exist "%ORIG%" set "ORIG=%SystemRoot%\System32\wsock32.dll"
if not exist "%ORIG%" goto origmiss
copy /Y "%ORIG%" "%GAME%\wsock32_orig.dll" >nul
if errorlevel 1 goto copyfail
echo [2/3] copied wsock32_orig.dll from %ORIG%
goto install
:origok
echo [2/3] wsock32_orig.dll already there, skipped
goto install
:origmiss
echo.
echo [!] System wsock32.dll not found on this machine.
echo     Copy C:/Windows/SysWOW64/wsock32.dll into the game folder
echo     and rename it to wsock32_orig.dll, then run this again.
echo.
pause
exit /b 1

rem ---- 3/3 install the mod ----
:install
copy /Y "%SRC%wsock32.dll" "%GAME%\wsock32.dll" >nul
if errorlevel 1 goto copyfail
echo [3/3] wsock32.dll installed

echo.
echo Done. Just launch PlantsVsZombies.exe and play.
echo.
echo   Button "hextech" is at the bottom-right of the game window
echo   F7 = toggle HP display    F8 = toggle detail panel
echo.
echo If your antivirus flags or deletes wsock32.dll, whitelist it.
echo Both wsock32.dll and wsock32_orig.dll are required to run.
echo.
echo Read the txt file in this folder for details (Chinese).
echo.
pause
exit /b 0

:noexe
echo.
echo [!] No PlantsVsZombies.exe in:
echo     %GAME%
echo     Drag the game FOLDER here, not the .exe.
echo.
pause
exit /b 1

:nodll
echo.
echo [!] wsock32.dll is missing from this package.
echo.
pause
exit /b 1

:copyfail
echo.
echo [!] Copy failed - the game may still be running.
echo     Close it completely and run this again.
echo.
pause
exit /b 1
