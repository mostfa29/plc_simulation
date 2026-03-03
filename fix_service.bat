@echo off
REM =========================================================
REM TopDrive AI - Fix CMD Flashing / Remove Old Service
REM Double-click this FIRST to stop the flashing
REM =========================================================

echo.
echo  TopDrive AI - Service Fix
echo  =========================
echo.

REM Step 1: Kill all running Python processes for rig_monitor
echo  [1/4] Killing running processes...
taskkill /IM pythonw.exe /F >nul 2>nul
taskkill /IM python.exe /F >nul 2>nul
echo  Done.
echo.

REM Step 2: Remove the old Task Scheduler entry (this causes the flashing)
echo  [2/4] Removing old Task Scheduler entry...
schtasks /DELETE /TN "TopDriveAI_RigMonitor" /F >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo  Removed: TopDriveAI_RigMonitor
) else (
    echo  Not found (already removed)
)
echo.

REM Step 3: Remove startup shortcut if exists
echo  [3/4] Cleaning startup folder...
set SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\TopDriveAI_RigMonitor.lnk
if exist "%SHORTCUT%" (
    del "%SHORTCUT%"
    echo  Removed startup shortcut
) else (
    echo  No startup shortcut found
)
echo.

REM Step 4: Clean up stale files
echo  [4/4] Cleaning stale status files...
cd /d "%~dp0"
if exist monitor_status.json del monitor_status.json
if exist monitor_stop.signal del monitor_stop.signal
echo  Done.
echo.

echo  ==========================================
echo  FIXED! CMD flashing should stop now.
echo  ==========================================
echo.
echo  To reinstall the service properly:
echo    Double-click install_service.bat
echo.
echo  To run capture manually instead:
echo    python capture_live.py --host 129.168.1.25 --profile profiles\precision_rig_707_3pd_ht.yaml --smart --hz 20
echo.
pause
