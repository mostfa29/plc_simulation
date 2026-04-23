@echo off
TITLE HXI - Service Status
sc query HXIOptimizer 2>NUL
IF %ERRORLEVEL% NEQ 0 (
  echo.
  echo HXIOptimizer service is NOT installed.
  echo Run A_install_service.bat ^(as Administrator^) to install.
  echo.
) ELSE (
  echo.
  echo Failure counter ^(last 24h^):
  sc qfailure HXIOptimizer 2>NUL | findstr /V "^$"
)
echo.
pause
