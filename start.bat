@echo off
setlocal
cd /d "%~dp0"
where docker >nul 2>&1
if errorlevel 1 goto docker_missing
docker info >nul 2>&1
if errorlevel 1 goto docker_missing
echo Starting Azar Dandeh local dashboard...
docker compose -p azar-dandeh -f compose.dashboard.yml up -d --build --wait --wait-timeout 180
if errorlevel 1 goto failed
start "" "http://localhost:8090"
echo Dashboard: http://localhost:8090
pause
exit /b 0
:docker_missing
echo Install Docker Desktop and start it with Linux containers, then retry.
pause
exit /b 1
:failed
echo Startup failed. Check logs below. Your saved data has not been deleted.
docker compose -p azar-dandeh -f compose.dashboard.yml logs --tail 50
pause
exit /b 1
