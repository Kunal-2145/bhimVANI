@echo off
REM ============================================================
REM BimaVani (बीमा-वाणी) 1-click startup (Windows)
REM   run.bat            -> create venv if needed, install deps, start server
REM   run.bat --test     -> run the pytest suite instead
REM   run.bat --verify   -> run live Gnani API smoke test (needs GNANI_API_KEY)
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [run.bat] Creating virtualenv...
    python -m venv .venv
)

echo [run.bat] Installing/upgrading dependencies...
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt

if not exist "backend\cache\tts" mkdir "backend\cache\tts"
if not exist "backend\data\call_history.json" (
    echo []> "backend\data\call_history.json"
)

if /i "%~1"=="--test" (
    .venv\Scripts\python.exe -m pytest -q tests\
    goto :EOF
)
if /i "%~1"=="--verify" (
    .venv\Scripts\python.exe verify_gnani_api.py
    goto :EOF
)

if exist ".env" (
    echo [run.bat] Loading .env
    for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
        set "k=%%~A"
        set "v=%%~B"
        if not "!k!"=="" set "!k!=!v!"
    )
) else (
    echo [run.bat] NOTE: no .env found -^> Credit-Shield Fallback Mode ^(demo-safe^).
    echo          Copy .env.example -^> .env and add GNANI_API_KEY for live Gnani.
)

if "%UVICORN_HOST%"=="" set "UVICORN_HOST=127.0.0.1"
if "%UVICORN_PORT%"=="" set "UVICORN_PORT=8000"

echo [run.bat] Starting BimaVani on http://%UVICORN_HOST%:%UVICORN_PORT%  (Ctrl+C to stop)
.venv\Scripts\python.exe -m uvicorn backend.main:app --host %UVICORN_HOST% --port %UVICORN_PORT% --reload
