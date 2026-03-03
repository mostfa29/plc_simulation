@echo off
REM =========================================================
REM TopDrive AI - Install Rig Monitor Service
REM Run this ONCE as Administrator to enable auto-start
REM =========================================================

echo.
echo  TopDrive AI - Rig Monitor Installer
echo  ====================================
echo.

REM Find Python
where pythonw >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo  ERROR: pythonw.exe not found in PATH
    echo  Make sure Python is installed and in your PATH.
    pause
    exit /b 1
)

REM Get paths
for /f "delims=" %%i in ('where pythonw') do set PYTHONW=%%i
for /f "delims=" %%i in ('where python') do set PYTHON=%%i
set SCRIPT=%~dp0rig_monitor.py

if not exist "%SCRIPT%" (
    echo  ERROR: rig_monitor.py not found at %SCRIPT%
    pause
    exit /b 1
)

echo  Python:  %PYTHON%
echo  Pythonw: %PYTHONW%
echo  Script:  %SCRIPT%
echo.

REM Install Rich (one-time)
echo  Installing 'rich' package...
"%PYTHON%" -m pip install rich --quiet
echo  Done.
echo.

REM Create scheduled task
echo  Creating Windows Task Scheduler entry...
schtasks /CREATE /TN "TopDriveAI_RigMonitor" ^
    /TR "\"%PYTHONW%\" \"%SCRIPT%\" --service" ^
    /SC ONLOGON ^
    /DELAY 0000:30 ^
    /F ^
    /RL HIGHEST

if %ERRORLEVEL% EQU 0 (
    echo.
    echo  ==========================================
    echo  SUCCESS: Rig Monitor service installed!
    echo  ==========================================
    echo.
    echo  It will auto-start 30 seconds after login.
    echo.
    echo  To start NOW:    schtasks /RUN /TN "TopDriveAI_RigMonitor"
    echo  To open dashboard: double-click launch_dashboard.bat
    echo  To stop:         python rig_monitor.py --stop
    echo  To uninstall:    python rig_monitor.py --uninstall
) else (
    echo.
    echo  FAILED: Could not create scheduled task.
    echo  Make sure you ran this as Administrator.
    echo  Right-click this file and select "Run as administrator"
)

echo.
pause
