@echo off
chcp 65001 >nul
echo Starting CompeteWatch...
echo.

cd /d "%~dp0backend"

REM Check if virtual environment exists
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)

REM Activate virtual environment
call .venv\Scripts\activate.bat

REM Install dependencies
echo Installing dependencies...
pip install -q -r requirements.txt

echo.
echo ========================================
echo CompeteWatch is starting...
echo Open http://localhost:8080 in browser
echo Press Ctrl+C to stop
echo ========================================
echo.

python main.py
pause
