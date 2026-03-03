@echo off
REM =========================================================
REM TopDrive AI - Install Rig Monitor Service
REM Double-click to set up auto-start on login
REM No admin required - uses Startup folder method
REM =========================================================

echo.
echo  TopDrive AI - Rig Monitor Installer
echo  ====================================
echo.

REM Find Python
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo  ERROR: python.exe not found in PATH
    echo  Make sure Python is installed and in your PATH.
    pause
    exit /b 1
)

for /f "delims=" %%i in ('where python') do set PYTHON=%%i
set SCRIPT_DIR=%~dp0
set VBS=%SCRIPT_DIR%start_hidden.vbs

if not exist "%VBS%" (
    echo  ERROR: start_hidden.vbs not found at %VBS%
    pause
    exit /b 1
)

echo  Python:  %PYTHON%
echo  VBS:     %VBS%
echo.

REM Step 1: Remove old Task Scheduler entry if it exists
echo  Removing old Task Scheduler entry (if any)...
schtasks /DELETE /TN "TopDriveAI_RigMonitor" /F >nul 2>nul
echo  Done.
echo.

REM Step 2: Kill any running service
echo  Stopping any running service...
taskkill /IM pythonw.exe /F >nul 2>nul
echo  Done.
echo.

REM Step 3: Install Rich
echo  Installing 'rich' package...
"%PYTHON%" -m pip install rich --quiet
echo  Done.
echo.

REM Step 4: Create shortcut in Windows Startup folder
echo  Creating Startup shortcut...
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set SHORTCUT=%STARTUP%\TopDriveAI_RigMonitor.lnk

REM Use PowerShell to create a proper .lnk shortcut
powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT%'); $s.TargetPath = '%VBS%'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = 'TopDrive AI Rig Monitor'; $s.Save()"

if exist "%SHORTCUT%" (
    echo.
    echo  ==========================================
    echo  SUCCESS: Rig Monitor auto-start installed!
    echo  ==========================================
    echo.
    echo  Method:  Startup folder shortcut (no admin needed)
    echo  Shortcut: %SHORTCUT%
    echo.
    echo  It will auto-start when you log in.
    echo.
    echo  To start NOW:      double-click start_hidden.vbs
    echo  To open dashboard: double-click launch_dashboard.bat
    echo  To stop service:   double-click stop_service.bat
    echo  To uninstall:      python rig_monitor.py --uninstall
) else (
    echo.
    echo  FAILED: Could not create startup shortcut.
    echo  You can manually copy start_hidden.vbs to:
    echo    %STARTUP%
)

echo.
pause
