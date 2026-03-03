@echo off
REM TopDrive AI - Rig Monitor Dashboard
REM Double-click to open the live dashboard
title TopDrive AI - Rig Monitor
cd /d "%~dp0"
python rig_monitor.py --dashboard
pause
