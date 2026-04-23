@echo off
TITLE HXI - Status
CALL "%~dp0_common.bat"

echo =====================================================================
echo   HXI Optimizer - STATUS
echo =====================================================================
echo.

REM Check service mode first
sc query HXIOptimizer >NUL 2>&1
IF %ERRORLEVEL% EQU 0 (
  echo Service mode (NSSM):
  sc query HXIOptimizer | findstr /R "STATE SERVICE_NAME"
  echo.
)

%PYTHON% "%~dp0_helpers.py" show_status

echo.
echo Last 20 log lines:
echo =====================================================================
IF EXIST "hxi_optimizer\logs\optimizer.log" (
  %PYTHON% -c "print(''.join(open('hxi_optimizer/logs/optimizer.log',encoding='utf-8',errors='replace').readlines()[-20:]))"
) ELSE (
  echo   (log file not yet created)
)

echo.
pause
POPD
