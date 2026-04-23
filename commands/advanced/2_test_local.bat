@echo off
TITLE HXI - Local Test
CALL "%~dp0_common.bat"

echo =====================================================================
echo   HXI Optimizer - LOCAL TEST MODE (no PLC, no VPN needed)
echo =====================================================================
echo.
echo This boots a fake PLC simulator on your computer, then starts the
echo optimizer + dashboard against it. Safe for learning the dashboard.
echo.
echo Two command windows will open. Leave them running.
echo To stop: close both windows (or Ctrl-C in each).
echo.
pause

echo.
echo Preparing local-test config...
%PYTHON% "%~dp0_helpers.py" setup_local_config

echo.
echo Starting simulator window...
START "HXI Simulator" cmd /k "%PYTHON% -m local_test.sim_plc --port 5020"

echo Waiting for simulator (up to 15s)...
%PYTHON% "%~dp0_helpers.py" wait_port 127.0.0.1 5020 15
IF %ERRORLEVEL% NEQ 0 (
  echo.
  echo [ERROR] Simulator didn't come up. Check the Simulator window for errors.
  pause
  exit /b 1
)
echo   OK - simulator is up.

echo Starting optimizer window...
START "HXI Optimizer" cmd /k "%PYTHON% -m hxi_optimizer.main"

echo Waiting for dashboard (up to 15s)...
%PYTHON% "%~dp0_helpers.py" wait_port 127.0.0.1 8420 15
IF %ERRORLEVEL% NEQ 0 (
  echo.
  echo [WARNING] Dashboard didn't come up. Check the Optimizer window.
  pause
  exit /b 1
)
echo   OK - dashboard is up.

echo.
echo Opening dashboard in your browser...
start "" "http://localhost:8420"

echo.
echo =====================================================================
echo   RUNNING. Two windows are open (Simulator + Optimizer).
echo   Dashboard: http://localhost:8420
echo.
echo   To stop: close both windows, or press Ctrl-C in each.
echo =====================================================================
echo.
pause
POPD
