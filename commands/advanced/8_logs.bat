@echo off
TITLE HXI - Live Logs (Ctrl-C to exit)
CALL "%~dp0_common.bat"

SET "LOG=hxi_optimizer\logs\optimizer.log"

echo =====================================================================
echo   Live log tail - Press Ctrl-C to exit
echo   File: %REPO_ROOT%\%LOG%
echo =====================================================================
echo.

%PYTHON% "%~dp0_tail_log.py" "%LOG%"
POPD
