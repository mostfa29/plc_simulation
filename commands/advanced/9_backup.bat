@echo off
TITLE HXI - Backup Data
CALL "%~dp0_common.bat"

echo =====================================================================
echo   HXI Optimizer - Backup CSVs + audit + state
echo =====================================================================
echo.

SET "BACKUP_DIR=%USERPROFILE%\Documents\HXI_Backups"
IF NOT EXIST "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

FOR /F "delims=" %%t IN ('%PYTHON% -c "from datetime import datetime; print(datetime.now().strftime('%%Y%%m%%d_%%H%%M%%S'))"') DO SET "STAMP=%%t"

SET "BACKUP_FILE=%BACKUP_DIR%\hxi_backup_%STAMP%.zip"

echo Creating backup: %BACKUP_FILE%
echo.

%PYTHON% "%~dp0_helpers.py" backup "%BACKUP_FILE%"

IF %ERRORLEVEL% NEQ 0 (
  echo [ERROR] Backup failed.
  pause
  exit /b 1
)

echo.
echo Backup saved to:
echo   %BACKUP_FILE%
echo.
echo Opening backup folder...
start "" "%BACKUP_DIR%"

pause
POPD
