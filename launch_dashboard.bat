@echo off
REM TopDrive AI - Rig Monitor Dashboard
REM Double-click to open the live dashboard in your browser
title TopDrive AI - Dashboard Server
cd /d "%~dp0"
python rig_monitor.py --web
pause
