@echo off
setlocal

echo ===============================================
echo  EcoVision Sentinel -- Docker stack
echo ===============================================

where docker >nul 2>nul
if errorlevel 1 goto :nodocker

if not exist ".env" goto :noenv

echo.
echo Building and starting postgres, backend, detector, frontend (with GPU passthrough)...
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
if errorlevel 1 goto :buildfail

echo.
echo Waiting a few seconds for backend to initialize...
timeout /t 5 /nobreak >nul

echo.
echo === Service status ===
docker compose ps

echo.
echo === Backend logs (look for the BOOTSTRAP line) ===
docker compose logs backend --tail 40

echo.
echo ===============================================
echo  Done. Frontend: http://localhost:3000
echo  Backend API:    http://localhost:8000
echo  Detector:       http://localhost:8001
echo.
echo  To watch live logs:   docker compose logs -f
echo  To stop everything:   docker compose down
echo ===============================================
goto :end

:nodocker
echo ERROR: Docker not found on PATH. Is Docker Desktop installed and running?
exit /b 1

:noenv
echo ERROR: .env not found in this folder. Create it first (see .env.example).
exit /b 1

:buildfail
echo.
echo Build/start failed -- see the error above.
exit /b 1

:end
endlocal