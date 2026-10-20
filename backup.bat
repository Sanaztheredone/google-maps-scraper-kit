@echo off
setlocal
cd /d "%~dp0"
if not exist backups mkdir backups
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "STAMP=%%i"
set "TARGET=backups\azar-dandeh-%STAMP%.sqlite3"
docker compose -p azar-dandeh -f compose.dashboard.yml exec -T dashboard python -m dashboard.backup > "%TARGET%.tmp"
if errorlevel 1 (
 del "%TARGET%.tmp" 2>nul
 echo Backup failed. Start the dashboard and retry.
 pause
 exit /b 1
)
move /y "%TARGET%.tmp" "%TARGET%" >nul
echo Backup saved: %TARGET%
echo Keep a copy on a separate drive. It contains business contact data.
pause
