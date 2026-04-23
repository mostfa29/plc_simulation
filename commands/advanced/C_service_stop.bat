@echo off
TITLE HXI - Stop Service
net session >NUL 2>&1
IF %ERRORLEVEL% NEQ 0 (
  echo Run this as Administrator.
  pause
  exit /b 1
)
echo Stopping HXIOptimizer service...
sc stop HXIOptimizer
timeout /t 3 /nobreak >NUL
sc query HXIOptimizer | findstr STATE
echo.
pause
