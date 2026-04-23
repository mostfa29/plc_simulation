@echo off
TITLE HXI - Edit Config
CALL "%~dp0_common.bat"

echo =====================================================================
echo   Edit hxi_optimizer\hxi_config.json
echo =====================================================================
echo.

IF NOT EXIST "hxi_optimizer\hxi_config.json" (
  echo Config doesn't exist - creating from template...
  copy /Y "hxi_optimizer\hxi_config.template.json" "hxi_optimizer\hxi_config.json" >NUL
)

echo Fields you need to set:
echo.
echo   plc_host     : the PLC IP you see in eCatcher (e.g. 192.168.1.10)
echo   phase        : "A" (observer), "B" (advisory), "C" (limited writes), "D" (full)
echo   deadband_rpm : from commissioning test 4 noise-floor result
echo.
echo   safety.abs_min_lower  : Steve's signed-off minimum for %%R06603
echo   safety.abs_max_lower  : Steve's signed-off maximum for %%R06603
echo   safety.abs_min_upper  : Steve's signed-off minimum for %%R06604
echo   safety.abs_max_upper  : Steve's signed-off maximum for %%R06604
echo.
echo Save and close Notepad to apply.
echo.
pause

notepad "hxi_optimizer\hxi_config.json"

echo.
echo Validating...
%PYTHON% -c "import json; c=json.load(open('hxi_optimizer/hxi_config.json')); print('  plc_host :', c.get('plc_host')); print('  phase    :', c.get('phase')); print('  safety   :', c.get('safety')); print('  OK - config is valid JSON')"
IF %ERRORLEVEL% NEQ 0 (
  echo.
  echo [ERROR] JSON is invalid. Edit again or ask Moe.
)
echo.
pause
POPD
