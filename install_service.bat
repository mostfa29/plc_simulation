@echo off
setlocal enabledelayedexpansion
REM =========================================================
REM TopDrive AI - Install Rig Monitor Service
REM Double-click to set up auto-start on login
REM No admin required - uses Startup folder method
REM =========================================================

echo.
echo  TopDrive AI - Rig Monitor Installer
echo  ====================================
echo.

set SCRIPT_DIR=%~dp0
set VBS=%SCRIPT_DIR%start_hidden.vbs
set PYTHON=
set PYTHONW=

if not exist "%VBS%" (
    echo  ERROR: start_hidden.vbs not found at %VBS%
    pause
    exit /b 1
)

REM ---- Find a working Python installation ----

REM Method 1: Try 'py' launcher (most reliable on Windows)
py -3 --version >nul 2>nul
if !ERRORLEVEL! EQU 0 (
    for /f "delims=" %%i in ('py -3 -c "import sys,os; print(sys.executable)"') do set PYTHON=%%i
    for /f "delims=" %%i in ('py -3 -c "import sys,os; print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set PYTHONW=%%i
    goto :found_python
)

REM Method 2: Check each 'where python' result, skip Windows Store alias
for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PYTHON (
        echo "%%i" | findstr /i "WindowsApps" >nul
        if !ERRORLEVEL! NEQ 0 (
            "%%i" --version >nul 2>nul
            if !ERRORLEVEL! EQU 0 (
                set PYTHON=%%i
            )
        )
    )
)
if defined PYTHON goto :derive_pythonw

REM Method 3: Check common install locations
for %%d in (
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "C:\Python314\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
    "C:\Program Files\Python314\python.exe"
    "C:\Program Files\Python313\python.exe"
    "C:\Program Files\Python312\python.exe"
    "C:\Program Files\Python311\python.exe"
    "C:\Program Files\Python310\python.exe"
) do (
    if not defined PYTHON (
        if exist %%d set PYTHON=%%~d
    )
)
if defined PYTHON goto :derive_pythonw

echo  ERROR: Could not find a working Python installation.
echo.
echo  Please install Python from https://python.org
echo  Make sure to check "Add Python to PATH" during install.
echo.
pause
exit /b 1

:derive_pythonw
REM Get pythonw.exe from same directory as python.exe
for %%p in ("%PYTHON%") do set PYTHONW=%%~dppythonw.exe

:found_python
echo  Python:   %PYTHON%
echo  Pythonw:  %PYTHONW%
echo  VBS:      %VBS%
echo.

REM Step 1: Remove old Task Scheduler entry if it exists
echo  [1/5] Removing old Task Scheduler entry (if any)...
schtasks /DELETE /TN "TopDriveAI_RigMonitor" /F >nul 2>nul
echo  Done.
echo.

REM Step 2: Kill any running service
echo  [2/5] Stopping any running service...
taskkill /IM pythonw.exe /F >nul 2>nul
echo  Done.
echo.

REM Step 3: Install Rich
echo  [3/5] Installing 'rich' package...
"%PYTHON%" -m pip install rich --quiet 2>nul
if !ERRORLEVEL! NEQ 0 (
    echo  Warning: pip install failed, but continuing...
)
echo  Done.
echo.

REM Step 4: Write pythonw path into VBS for reliable discovery
echo  [4/5] Configuring pythonw path...
REM Create a small config file so VBS can find the right pythonw
echo %PYTHONW%> "%SCRIPT_DIR%pythonw_path.txt"
echo  Saved to pythonw_path.txt
echo.

REM Step 5: Create shortcut in Windows Startup folder
echo  [5/5] Creating Startup shortcut...
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
    echo  Method:   Startup folder shortcut (no admin needed)
    echo  Shortcut: %SHORTCUT%
    echo.
    echo  It will auto-start when you log in.
    echo.
    echo  To start NOW:      double-click start_hidden.vbs
    echo  To open dashboard: double-click launch_dashboard.bat
    echo  To stop service:   double-click stop_service.bat
) else (
    echo.
    echo  WARNING: Could not create startup shortcut automatically.
    echo  Please manually copy start_hidden.vbs to:
    echo    %STARTUP%
    echo.
    echo  The service will still work if you double-click start_hidden.vbs
)

echo.
pause
