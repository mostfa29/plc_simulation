@echo off
TITLE HXI - Uninstall Service
CALL "%~dp0_common.bat"

net session >NUL 2>&1
IF %ERRORLEVEL% NEQ 0 (
  echo Run this as Administrator.
  pause
  exit /b 1
)

echo =====================================================================
echo   HXI Optimizer - UNINSTALL service
echo =====================================================================
echo.
echo This removes the Windows Service entirely. The optimizer will no
echo longer start on boot. CSV logs and audit trail are kept.
echo.
SET /P GO="Are you sure? Type 'yes' exactly: "
IF NOT "%GO%"=="yes" (
  echo Cancelled.
  pause
  exit /b 0
)

CALL "hxi_optimizer\deploy\uninstall_service.bat"
echo.
pause
POPD
