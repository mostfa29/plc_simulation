@echo off
TITLE HXI - Open Dashboard
CALL "%~dp0_common.bat"

REM Quick check that something's listening
%PYTHON% -c "import socket; s=socket.socket(); s.settimeout(1); import sys; sys.exit(0 if s.connect_ex(('127.0.0.1',8420))==0 else 1)" 2>NUL
IF %ERRORLEVEL% NEQ 0 (
  echo.
  echo The optimizer isn't running - dashboard won't load.
  echo.
  echo Run 4_start.bat first, then try this again.
  echo.
  pause
  exit /b 0
)

echo Opening http://localhost:8420 ...
start "" "http://localhost:8420"
timeout /t 2 /nobreak >NUL
POPD
