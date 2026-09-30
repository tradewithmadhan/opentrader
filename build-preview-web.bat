@echo off
REM preview.bat — production build + static preview (fast, no hot reload).
REM Uses the prebuilt files in dist\. Open http://localhost:4170/
REM Rebuilds first so the preview always matches current src.
setlocal
cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
  echo [preview] Node.js not found. Install Node 22+ from https://nodejs.org/ and retry.
  pause
  exit /b 1
)

if not exist "node_modules" (
  echo [preview] First run: installing dependencies - one-time, about 20s...
  call npm ci --no-audit --no-fund
  if errorlevel 1 (
    echo [preview] npm ci failed. Check your network and retry.
    pause
    exit /b 1
  )
)

echo [preview] Building... takes a few minutes, one time per src change.
call npm run build
if errorlevel 1 (
  echo [preview] Build failed. See errors above.
  pause
  exit /b 1
)

echo.
echo [preview] Serving dist - open the Local URL printed below. Press Ctrl+C to stop.
echo.
call npm run serve
