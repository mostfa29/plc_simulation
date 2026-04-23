@echo off
TITLE HXI - First-time Setup
CALL "%~dp0_common.bat"

echo =====================================================================
echo   HXI Optimizer - First-time Setup
echo =====================================================================
echo.
echo This installs every Python package the system needs.
echo It's safe to re-run this any time.
echo.
echo Takes 2-3 minutes on a fast connection.
echo.
pause

echo.
echo Installing packages...
%PYTHON% -m pip install --upgrade pip
%PYTHON% -m pip install pymodbus==3.13.* numpy fastapi uvicorn openpyxl paramiko psutil pyyaml asyncua websocket-client

IF %ERRORLEVEL% NEQ 0 (
  echo.
  echo [ERROR] One or more packages failed to install.
  echo Check your internet connection and try again.
  pause
  exit /b 1
)

echo.
echo =====================================================================
echo   SETUP COMPLETE
echo =====================================================================
echo.
echo Verifying...
%PYTHON% -c "import pymodbus, numpy, fastapi, uvicorn, openpyxl, paramiko, psutil, yaml, asyncua; print('All packages imported OK')"
IF %ERRORLEVEL% NEQ 0 (
  echo [ERROR] Verification failed.
  pause
  exit /b 1
)

echo.
echo You're ready. Next steps:
echo   - Practice offline:   double-click 2_test_local.bat
echo   - On the rig:         connect eCatcher, then run 3_commission.bat
echo.
pause
POPD
