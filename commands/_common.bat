@echo off
REM Shared helpers. Included by every other .bat via CALL _common.bat

REM Change to repo root (parent of commands/)
SET "REPO_ROOT=%~dp0.."
PUSHD "%REPO_ROOT%"

REM Locate Python
SET "PYTHON="
WHERE python >NUL 2>&1 && SET "PYTHON=python"
IF "%PYTHON%"=="" WHERE py >NUL 2>&1 && SET "PYTHON=py -3"
IF "%PYTHON%"=="" (
  IF EXIST "C:\Python311\python.exe" SET "PYTHON=C:\Python311\python.exe"
)
IF "%PYTHON%"=="" (
  echo.
  echo [ERROR] Python not found in PATH or at C:\Python311\python.exe
  echo.
  echo Install Python 3.11 from https://www.python.org/downloads/windows/
  echo and make sure "Add Python to PATH" is checked during install.
  echo.
  pause
  exit /b 1
)
GOTO :EOF
