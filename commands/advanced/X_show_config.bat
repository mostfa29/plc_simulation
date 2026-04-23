@echo off
TITLE HXI - Show Config
CALL "%~dp0_common.bat"

IF NOT EXIST "hxi_optimizer\hxi_config.json" (
  echo Config file doesn't exist yet.
  echo.
  echo Run X_edit_config.bat to create one.
  pause
  exit /b 0
)

echo =====================================================================
echo   Current config: hxi_optimizer\hxi_config.json
echo =====================================================================
echo.
%PYTHON% -c "import json; print(open('hxi_optimizer/hxi_config.json').read())"
echo.
echo To edit: X_edit_config.bat
echo.
pause
POPD
