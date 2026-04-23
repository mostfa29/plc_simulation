@echo off
TITLE HXI Optimizer - Running
CALL "%~dp0_common.bat"

echo =====================================================================
echo   HXI Optimizer - START
echo =====================================================================
echo.

REM Check config exists
IF NOT EXIST "hxi_optimizer\hxi_config.json" (
  echo [ERROR] hxi_optimizer\hxi_config.json not found.
  echo.
  echo Run X_edit_config.bat to set up the config first.
  pause
  exit /b 1
)

REM Extract current phase + host for display
%PYTHON% -c "import json; c=json.load(open('hxi_optimizer/hxi_config.json')); print('  PLC host : ', c.get('plc_host')); print('  Phase    : ', c.get('phase')); print('  Transport: ', c.get('transport','modbus'))"

echo.
SET /P GO="Start the optimizer now? (y/n): "
IF /I NOT "%GO%"=="y" (
  echo Cancelled.
  pause
  exit /b 0
)

REM Check if already running (port 8420 in use)
%PYTHON% -c "import socket; s=socket.socket(); s.settimeout(1); import sys; sys.exit(0 if s.connect_ex(('127.0.0.1',8420))==0 else 1)" 2>NUL
IF %ERRORLEVEL% EQU 0 (
  echo.
  echo [WARNING] Dashboard port 8420 is already in use - optimizer may already be running.
  echo.
  SET /P FORCE="Start anyway? (y/n): "
  IF /I NOT "%FORCE%"=="y" exit /b 0
)

echo.
echo Starting optimizer in new window...
START "HXI Optimizer" cmd /k "%PYTHON% -m hxi_optimizer.main"

echo Waiting for dashboard to come up...
timeout /t 6 /nobreak >NUL

REM Confirm it's listening
%PYTHON% -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8420/api/status',timeout=3); import json; d=json.loads(r.read()); print('  Connection healthy:', d['connection']['healthy']); print('  State:', d['state_machine']); print('  Phase:', d['phase'])" 2>NUL
IF %ERRORLEVEL% NEQ 0 (
  echo.
  echo [WARNING] Dashboard didn't come up within 6 seconds.
  echo Check the Optimizer window for errors.
  pause
  exit /b 1
)

echo.
echo Opening dashboard...
start "" "http://localhost:8420"

echo.
echo =====================================================================
echo   RUNNING. Optimizer is in its own window.
echo   Dashboard: http://localhost:8420
echo.
echo   To stop:        5_stop.bat
echo   To check:       6_status.bat
echo   To see logs:    8_logs.bat
echo =====================================================================
echo.
pause
POPD
