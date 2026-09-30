@echo off
REM dev.bat — web dev with hot reload (browser, sample NSE/BSE data, no backend needed).
REM Usage: double-click dev.bat, then open http://localhost:1420/
setlocal
cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
  echo [dev] Node.js not found. Install Node 22+ from https://nodejs.org/ and retry.
  pause
  exit /b 1
)

if not exist "node_modules" (
  echo [dev] First run: installing dependencies - one-time, about 20s...
  call npm ci --no-audit --no-fund
  if errorlevel 1 (
    echo [dev] npm ci failed. Check your network and retry.
    pause
    exit /b 1
  )
)

if not exist "..\lightweight-charts-drawing\src\tv" (
  echo [dev] First run: cloning drawing core next to this folder...
  git clone https://github.com/deepentropy/lightweight-charts-drawing.git ..\lightweight-charts-drawing
  if errorlevel 1 (
    echo [dev] git clone failed. Install git and check your network, then retry.
    pause
    exit /b 1
  )
)

echo.
echo [dev] Starting... open http://localhost:1420/ in your browser.
echo [dev] Sample NSE/BSE data is built in. Press Ctrl+C to stop.
echo.
call npm run dev
