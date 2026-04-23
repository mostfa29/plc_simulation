@echo off
TITLE HXI Optimizer
SETLOCAL ENABLEDELAYEDEXPANSION

REM ─── One-click control for the HXI Optimizer ─────────────────────────
REM Double-click this file. Pick a number. That's it.
REM Everything else is automated.

CD /D "%~dp0"

REM Locate Python
SET "PY="
WHERE python >NUL 2>&1 && SET "PY=python"
IF "%PY%"=="" WHERE py >NUL 2>&1 && SET "PY=py -3"
IF "%PY%"=="" IF EXIST "C:\Python311\python.exe" SET "PY=C:\Python311\python.exe"
IF "%PY%"=="" (
  echo.
  echo [ERROR] Python not found.
  echo Install Python 3.11+ from https://www.python.org/downloads/windows/
  echo and check "Add Python to PATH" during install.
  echo.
  pause
  exit /b 1
)

SET "AUTO=%~dp0commands\_auto.py"

:MENU
CLS
echo.
echo  +---------------------------------------------------------+
echo  ^|                                                         ^|
echo  ^|              HXI OPTIMIZER  -  Control                  ^|
echo  ^|                                                         ^|
echo  +---------------------------------------------------------+
echo.
echo    On the rig (VPN connected to PLC):
echo    -----------------------------------
echo      [1]  FULL AUTO - discover PLC, commission, configure,
echo           start. One-click bring-up on a new rig.
echo.
echo      [2]  START     - launch optimizer + open dashboard
echo      [3]  STATUS    - is it running? connection health, RPM
echo      [4]  STOP      - shut down optimizer
echo      [5]  DASHBOARD - open the browser dashboard
echo      [6]  LOGS      - live tail of the optimizer log
echo.
echo    Setup / maintenance:
echo    --------------------
echo      [7]  BOOTSTRAP   - install Python packages (run once)
echo      [8]  DISCOVER    - scan VPN for the PLC IP
echo      [9]  COMMISSION  - run 4 tests (needs PLC IP)
echo      [C]  CONFIGURE   - edit safety limits + settings
echo      [B]  BACKUP      - zip logs + audit + state
echo.
echo    Offline:
echo    --------
echo      [L]  LOCAL TEST  - simulator + optimizer (no PLC needed)
echo.
echo      [Q]  Quit
echo.
SET /P CH="  Pick: "

IF /I "!CH!"=="1" GOTO FULL
IF /I "!CH!"=="2" GOTO START
IF /I "!CH!"=="3" GOTO STATUS
IF /I "!CH!"=="4" GOTO STOP
IF /I "!CH!"=="5" GOTO DASH
IF /I "!CH!"=="6" GOTO LOGS
IF /I "!CH!"=="7" GOTO BOOTSTRAP
IF /I "!CH!"=="8" GOTO DISCOVER
IF /I "!CH!"=="9" GOTO COMMISSION
IF /I "!CH!"=="C" GOTO CONFIGURE
IF /I "!CH!"=="B" GOTO BACKUP
IF /I "!CH!"=="L" GOTO LOCAL
IF /I "!CH!"=="Q" EXIT /B 0
ECHO.
ECHO    Not a valid choice.
timeout /t 1 >NUL
GOTO MENU

:FULL
CLS
%PY% "%AUTO%" full
echo.
pause
GOTO MENU

:START
CLS
%PY% "%AUTO%" start
echo.
pause
GOTO MENU

:STATUS
CLS
%PY% "%AUTO%" status
echo.
pause
GOTO MENU

:STOP
CLS
%PY% "%AUTO%" stop
echo.
pause
GOTO MENU

:DASH
start "" "http://localhost:8420"
GOTO MENU

:LOGS
CLS
echo Tailing hxi_optimizer\logs\optimizer.log ... Press Ctrl-C to stop.
echo.
%PY% "%~dp0commands\_tail_log.py" "hxi_optimizer\logs\optimizer.log"
GOTO MENU

:BOOTSTRAP
CLS
%PY% "%AUTO%" bootstrap
echo.
pause
GOTO MENU

:DISCOVER
CLS
%PY% "%AUTO%" discover
echo.
pause
GOTO MENU

:COMMISSION
CLS
SET /P IP="Enter PLC IP (or leave blank to auto-discover): "
IF "!IP!"=="" (
  FOR /F "delims=" %%i IN ('%PY% "%AUTO%" discover 2^>NUL ^| findstr /R "^[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*$"') DO SET "IP=%%i"
)
IF "!IP!"=="" (
  echo No PLC IP available.
  pause
  GOTO MENU
)
%PY% "%AUTO%" commission --host !IP!
echo.
pause
GOTO MENU

:CONFIGURE
CLS
%PY% "%AUTO%" configure
echo.
pause
GOTO MENU

:BACKUP
CLS
%PY% "%~dp0commands\_helpers.py" backup "%USERPROFILE%\Documents\HXI_Backups\hxi_backup_manual.zip"
start "" "%USERPROFILE%\Documents\HXI_Backups"
echo.
pause
GOTO MENU

:LOCAL
CLS
echo Starting local simulator + optimizer (no PLC needed)...
%PY% "%~dp0commands\_helpers.py" setup_local_config

START "HXI Simulator" cmd /k "%PY% -m local_test.sim_plc --port 5020"
%PY% "%~dp0commands\_helpers.py" wait_port 127.0.0.1 5020 15 >NUL

START "HXI Optimizer" cmd /k "%PY% -m hxi_optimizer.main"
%PY% "%~dp0commands\_helpers.py" wait_port 127.0.0.1 8420 15 >NUL

start "" "http://localhost:8420"
echo.
echo Simulator + Optimizer started. Dashboard opened.
echo To stop: close both command windows.
echo.
pause
GOTO MENU
