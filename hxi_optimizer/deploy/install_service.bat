@echo off
REM ────────────────────────────────────────────────────────────────────────
REM HXI Optimizer — install as Windows Service via NSSM
REM Per MASTER_CONTEXT §8.1. Replaces the legacy Startup-folder + .vbs path.
REM
REM Prerequisites:
REM   - NSSM 2.24-101 installed at C:\tools\nssm.exe
REM   - Python 3.11 at C:\Python311\python.exe with pip installs:
REM       pymodbus==3.13.* numpy psutil
REM   - Source tree placed at C:\hxi_optimizer
REM ────────────────────────────────────────────────────────────────────────
SET NSSM=C:\tools\nssm.exe
SET PYTHON=C:\Python311\python.exe
SET ROOT=C:\hxi_optimizer
SET SCRIPT=%ROOT%\hxi_optimizer\main.py
SET NAME=HXIOptimizer

%NSSM% install %NAME% %PYTHON% %SCRIPT%
%NSSM% set %NAME% AppDirectory %ROOT%
%NSSM% set %NAME% AppStdout %ROOT%\hxi_optimizer\logs\service_stdout.log
%NSSM% set %NAME% AppStderr %ROOT%\hxi_optimizer\logs\service_stderr.log
%NSSM% set %NAME% AppStdoutCreationDisposition 4
%NSSM% set %NAME% AppStderrCreationDisposition 4
%NSSM% set %NAME% AppEnvironmentExtra PYTHONUNBUFFERED=1 PYTHONPATH=%ROOT%
%NSSM% set %NAME% Start SERVICE_DELAYED_AUTO_START
%NSSM% set %NAME% ObjectName LocalSystem

REM Restart policy: 5s → 30s → 60s backoff over 24-hour reset window
sc failure %NAME% reset= 86400 actions= restart/5000/restart/30000/restart/60000

%NSSM% start %NAME%
echo Service %NAME% installed and started.
echo Verify with: sc query %NAME%
