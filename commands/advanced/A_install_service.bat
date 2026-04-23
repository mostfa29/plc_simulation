@echo off
TITLE HXI - Install Windows Service
CALL "%~dp0_common.bat"

REM Must run as Administrator
net session >NUL 2>&1
IF %ERRORLEVEL% NEQ 0 (
  echo.
  echo [ERROR] This script must be run as Administrator.
  echo.
  echo Right-click A_install_service.bat and pick "Run as administrator".
  echo.
  pause
  exit /b 1
)

echo =====================================================================
echo   HXI Optimizer - Install as Windows Service (NSSM)
echo =====================================================================
echo.

REM Check NSSM is available
IF NOT EXIST "C:\tools\nssm.exe" (
  echo [ERROR] NSSM not found at C:\tools\nssm.exe
  echo.
  echo Download NSSM 2.24-101 from https://nssm.cc/download
  echo Extract nssm.exe to C:\tools\nssm.exe, then re-run this script.
  echo.
  pause
  exit /b 1
)

echo NSSM found at C:\tools\nssm.exe
echo.
echo This will install the service using the EXISTING install_service.bat
echo in hxi_optimizer\deploy\. It will use the config currently at:
echo   %REPO_ROOT%\hxi_optimizer\hxi_config.json
echo.
SET /P GO="Continue? (y/n): "
IF /I NOT "%GO%"=="y" (
  echo Cancelled.
  pause
  exit /b 0
)

CALL "hxi_optimizer\deploy\install_service.bat"

IF %ERRORLEVEL% EQU 0 (
  echo.
  echo =====================================================================
  echo   SERVICE INSTALLED
  echo =====================================================================
  echo.
  echo The optimizer will now start automatically when Windows boots.
  echo.
  echo Control commands:
  echo   B_service_start.bat   - Start the service now
  echo   C_service_stop.bat    - Stop the service
  echo   D_service_status.bat  - Is it running?
  echo   E_uninstall_service.bat - Remove entirely
  echo.
)
pause
POPD
