@echo off
TITLE HXI - Commissioning Tests
CALL "%~dp0_common.bat"

echo =====================================================================
echo   HXI Optimizer - Commissioning Tests (AGAINST REAL PLC)
echo =====================================================================
echo.
echo Prerequisites:
echo   [ ] eCatcher is connected (green tunnel status)
echo   [ ] You can ping the PLC
echo   [ ] You know the PLC's VPN-side IP address
echo.
echo This runs 4 tests. Safe - writes only to SPARE registers, never to
echo live swash bounds.
echo.

SET /P PLC_IP="Enter PLC IP address (e.g. 192.168.1.10): "
IF "%PLC_IP%"=="" (
  echo [ERROR] No IP provided.
  pause
  exit /b 1
)

echo.
echo Testing reachability...
ping -n 2 %PLC_IP% >NUL
IF %ERRORLEVEL% NEQ 0 (
  echo.
  echo [ERROR] Cannot ping %PLC_IP%
  echo   - Is eCatcher connected?
  echo   - Is the PLC powered on?
  echo   - Try: telnet %PLC_IP% 502
  echo.
  pause
  exit /b 1
)
echo   OK - ping responds.

echo.
echo Testing Modbus port 502 is open...
%PYTHON% -c "import socket; s=socket.socket(); s.settimeout(3); s.connect(('%PLC_IP%', 502)); print('  OK - port 502 reachable'); s.close()" 2>NUL
IF %ERRORLEVEL% NEQ 0 (
  echo.
  echo [ERROR] Port 502 not reachable. Likely eWon not in routing mode.
  echo Call Moe.
  pause
  exit /b 1
)

echo.
echo =====================================================================
echo   TEST 1 of 4: Byte Order
echo =====================================================================
echo   Writes 1234.5 to spare register %%R06630, reads back, determines byte order.
echo.
pause
%PYTHON% -m hxi_optimizer.deploy.commissioning_tests --test byte_order --host %PLC_IP%
IF %ERRORLEVEL% NEQ 0 (
  echo.
  echo [ERROR] Byte order test failed. Do NOT continue.
  echo Call Moe with the output above.
  pause
  exit /b 1
)

echo.
echo =====================================================================
echo   TEST 2 of 4: FC16 Paired-Write Atomicity (1000 trials)
echo =====================================================================
echo   Writes pairs of values to SPARE registers, checks for cross-faults.
echo.
pause
%PYTHON% -m hxi_optimizer.deploy.commissioning_tests --test fc16_atomicity --host %PLC_IP% --trials 1000

echo.
echo =====================================================================
echo   TEST 3 of 4: VPN Latency (100 reads)
echo =====================================================================
echo.
pause
%PYTHON% -m hxi_optimizer.deploy.commissioning_tests --test vpn_latency --host %PLC_IP%

echo.
echo =====================================================================
echo   TEST 4 of 4: RPM Noise Floor (60s at steady speed)
echo =====================================================================
echo.
echo Have the driller set the top drive to 60 RPM steady with NO drilling load.
echo.
SET /P READY="Type 'y' when the driller confirms, anything else to skip: "
IF /I "%READY%"=="y" (
  %PYTHON% -m hxi_optimizer.deploy.commissioning_tests --test noise_floor --host %PLC_IP% --rpm 60
) ELSE (
  echo Skipped noise-floor test. Re-run later with:
  echo   %PYTHON% -m hxi_optimizer.deploy.commissioning_tests --test noise_floor --host %PLC_IP% --rpm 60
)

echo.
echo =====================================================================
echo   COMMISSIONING COMPLETE
echo =====================================================================
echo.
echo Next steps:
echo   1. Note the byte order from Test 1 (ABCD or CDAB)
echo   2. Note the deadband value from Test 4
echo   3. Ask Steve for the 4 safety limits (abs_min/max_lower/upper)
echo   4. Run X_edit_config.bat to enter all these values
echo   5. Run 4_start.bat to start the optimizer in Phase A
echo.
pause
POPD
