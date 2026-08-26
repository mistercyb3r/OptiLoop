@echo off
echo.
echo   ========================================
echo      OptiLoop Setup (Windows)
echo   ========================================
echo.

REM --- Check Docker ---
where docker >nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] Docker found
) else (
    echo   [!!] Docker not found
    echo   Install from: https://docs.docker.com/get-docker/
    set /p cont="   Continue without Docker? (y/N): "
    if /i not "%cont%"=="y" exit /b 1
)

REM --- Check Python ---
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo   [!!] python not found. Install Python 3.11+.
    exit /b 1
)
echo   [OK] Python found

REM --- API Key ---
if not exist ".env" (
    copy .env.example .env >nul
    echo.
    echo   Set your OpenRouter API key:
    echo   (Get one free at https://openrouter.ai/keys)
    set /p apikey=   OPENROUTER_API_KEY: 
    if not "!apikey!"=="" (
        powershell -Command "(Get-Content .env) -replace 'your_openrouter_api_key_here','%apikey%' | Set-Content .env"
        echo   [OK] API key saved
    ) else (
        echo   [!!] No key entered. Edit .env manually.
    )
) else (
    echo   [OK] .env already exists
)

REM --- Install deps ---
echo.
echo   Installing Python dependencies...
pip install -r requirements.txt -q
echo   [OK] Dependencies installed

REM --- Run tests ---
echo.
echo   Running test suite...
pytest tests/ -q --tb=line
echo.

REM --- Docker Compose ---
where docker >nul 2>&1
if %errorlevel% equ 0 (
    set /p dc="   Start with Docker Compose? (Y/n): "
    if /i not "%dc%"=="n" (
        echo   Building and starting services...
        docker compose up -d --build
        echo   [OK] Backend:  http://localhost:8000
        echo   [OK] Dashboard: http://localhost:3000
    )
)

echo.
echo   ========================================
echo      Setup Complete!
echo   ========================================
