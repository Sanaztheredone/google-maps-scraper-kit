@echo off
setlocal
cd /d "%~dp0"
docker compose -p azar-dandeh -f compose.dashboard.yml stop
if errorlevel 1 (
 echo Could not stop services. Check Docker Desktop.
 pause
 exit /b 1
)
echo Stopped safely. Database and results are retained.
pause
