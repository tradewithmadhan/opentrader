@echo off
REM dev-tauri.bat — full desktop app with hot reload (needs Rust + MSVC build tools).
REM Prerequisites (one-time, ~10-12 GB on D:):
REM   1. Rust:  winget install Rustlang.Rustup   (then: rustup toolchain install stable)
REM   2. MSVC:  Visual Studio 2022 Build Tools with "Desktop development with C++"
REM              (install with --installPath on D: if C: is full)
REM   3. Token: copy .env.example to .env and set OPENTRADER_GATEWAY_TOKEN
REM              (without it the app runs but charts get no market data)
setlocal
cd /d "%~dp0"

where cargo >nul 2>nul
if errorlevel 1 (
  echo [dev] Rust (cargo) not found. See the prerequisites at the top of this file.
  pause
  exit /b 1
)

if not exist "node_modules" (
  echo [dev] First run: installing dependencies...
  call npm ci --no-audit --no-fund
  if errorlevel 1 (
    echo [dev] npm ci failed.
    pause
    exit /b 1
  )
)

if not exist ".env" (
  echo [dev] WARNING: no .env file - charts will load with no market data.
  echo [dev] Copy .env.example to .env and set OPENTRADER_GATEWAY_TOKEN to fix.
  echo.
)

echo [dev] Starting Tauri dev... first run compiles Rust (several minutes).
call npm run tauri dev
