@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Shipping document checker

where docker >nul 2>nul
if errorlevel 1 (
  echo.
  echo  Docker is not installed.
  echo  Install "Docker Desktop" from https://www.docker.com/products/docker-desktop/
  echo  then double-click start.bat again.
  echo.
  pause
  exit /b 1
)
docker info >nul 2>nul
if errorlevel 1 (
  echo.
  echo  Docker is installed but not running.
  echo  Open "Docker Desktop", wait until it says it is running, then double-click start.bat again.
  echo.
  pause
  exit /b 1
)

if not exist data mkdir data
if not exist out mkdir out

echo.
echo  Building and starting. The FIRST time takes a few minutes; after that it starts in seconds.
echo.
docker compose up --build -d
if errorlevel 1 (
  echo.
  echo  Something went wrong while building. Scroll up for the error and send it to the team.
  echo.
  pause
  exit /b 1
)

echo  Waiting for the app to be ready...
set /a tries=0
:wait
curl -s -f http://localhost:8501/_stcore/health >nul 2>nul
if not errorlevel 1 goto ready
set /a tries+=1
if !tries! GEQ 60 goto notready
timeout /t 2 /nobreak >nul
goto wait

:notready
echo.
echo  The app did not answer in time. Check it with:  docker compose logs
echo.
pause
exit /b 1

:ready
echo.
echo  ===========================================================
echo   READY
echo   On this computer:   http://localhost:8501
echo.
echo   From a phone on the same Wi-Fi, try one of:
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /R /C:"IPv4"') do (
  set "ip=%%a"
  echo        http://!ip: =!:8501
)
echo   (if the phone cannot connect, allow Docker through the Windows firewall)
echo  ===========================================================
echo.
start "" http://localhost:8501
echo  To stop it later, double-click stop.bat
pause
