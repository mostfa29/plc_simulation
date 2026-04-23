@echo off
TITLE HXI - Stop Optimizer
CALL "%~dp0_common.bat"

echo =====================================================================
echo   HXI Optimizer - STOP
echo =====================================================================
echo.

REM Check if the NSSM service is installed
sc query HXIOptimizer >NUL 2>&1
IF %ERRORLEVEL% EQU 0 (
  echo The optimizer is installed as a Windows Service.
  echo Use C_service_stop.bat to stop it properly.
  echo.
  pause
  exit /b 0
)

echo Stopping optimizer (foreground mode)...

REM Find the Python process running main.py and kill it
FOR /F "tokens=2 delims=," %%p IN ('wmic process where "CommandLine like '%%hxi_optimizer.main%%' and name='python.exe'" get ProcessId /format:csv 2^>NUL ^| findstr /R "[0-9]"') DO (
  echo   Stopping PID %%p...
  taskkill /PID %%p /T >NUL 2>&1
)

REM Also close the window titled "HXI Optimizer"
taskkill /FI "WindowTitle eq HXI Optimizer" /F >NUL 2>&1
taskkill /FI "WindowTitle eq HXI Optimizer*" /F >NUL 2>&1

REM Verify stopped
timeout /t 2 /nobreak >NUL
%PYTHON% -c "import socket; s=socket.socket(); s.settimeout(1); import sys; sys.exit(0 if s.connect_ex(('127.0.0.1',8420))==0 else 1)" 2>NUL
IF %ERRORLEVEL% EQU 0 (
  echo.
  echo [WARNING] Dashboard port 8420 is still active.
  echo Close the Optimizer window manually.
) ELSE (
  echo   OK - optimizer stopped.
)

echo.
pause
POPD
