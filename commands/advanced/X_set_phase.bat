@echo off
TITLE HXI - Change Phase
CALL "%~dp0_common.bat"

echo =====================================================================
echo   Change Operating Phase
echo =====================================================================
echo.
echo Phases:
echo   A - Observer (reads only, no writes)      DEFAULT
echo   B - Advisory (computes bounds but no writes)
echo   C - Limited writes (REQUIRES SIGN-OFF)
echo   D - Full authority (REQUIRES SIGN-OFF + 2 stands in C)
echo.

%PYTHON% -c "import json; c=json.load(open('hxi_optimizer/hxi_config.json')); print('Current phase:', c.get('phase'))"

echo.
SET /P NEWPHASE="New phase (A/B/C/D): "

IF /I "%NEWPHASE%"=="C" goto SIGNOFF
IF /I "%NEWPHASE%"=="D" goto SIGNOFF
goto APPLY

:SIGNOFF
echo.
echo =====================================================================
echo   WARNING: Phase %NEWPHASE% enables PLC WRITES
echo =====================================================================
echo.
echo You must have:
echo   [ ] All 25 items in MASTER_CONTEXT signed by Steve
echo   [ ] Drilling engineer present
echo   [ ] Safety limits confirmed in hxi_config.json
echo.
SET /P CONFIRM="Type 'I HAVE SIGN-OFF' exactly to continue: "
IF NOT "%CONFIRM%"=="I HAVE SIGN-OFF" (
  echo.
  echo Cancelled - no changes made.
  pause
  exit /b 0
)

:APPLY
%PYTHON% "%~dp0_helpers.py" set_phase %NEWPHASE%

echo.
echo Restart the optimizer for this to take effect:
echo   5_stop.bat  then  4_start.bat
echo.
echo (or, if running as a service:  C_service_stop.bat  then  B_service_start.bat)
echo.
pause
POPD
