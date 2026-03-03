@echo off
REM TopDrive AI - Stop Rig Monitor Service
REM Double-click to stop the background service
title TopDrive AI - Stop Service
cd /d "%~dp0"
echo.
echo  Stopping Rig Monitor service...
echo.
python rig_monitor.py --stop
echo.
REM Also kill any remaining pythonw processes running rig_monitor
for /f "tokens=2" %%a in ('tasklist /fi "imagename eq pythonw.exe" /fo list ^| findstr "PID"') do (
    wmic process where "ProcessId=%%a" get CommandLine 2>nul | findstr /i "rig_monitor" >nul && (
        echo  Killing pythonw PID %%a
        taskkill /PID %%a /F >nul 2>nul
    )
)
echo.
echo  Service stopped.
echo.
pause
