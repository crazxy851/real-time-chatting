@echo off
set "ROOT=%~dp0"
cd /d "%ROOT%"

echo Starting Sphere Nexus...

if not exist "%ROOT%.venv\Scripts\python.exe" (
    echo Creating Python virtual environment...
    python -m venv "%ROOT%.venv"
)

echo Installing Python requirements...
"%ROOT%.venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
"%ROOT%.venv\Scripts\python.exe" -m pip install -r "%ROOT%requirements.txt" >nul 2>&1

echo Launching services...

REM Start Python backend
cd /d "%ROOT%"
start /B "" "%ROOT%.venv\Scripts\python.exe" -m uvicorn main:app --app-dir "%ROOT%backend-python" --host 0.0.0.0 --port 8000

REM Start Frontend static server (Pointing directly to the public folder now)
cd /d "%ROOT%"
start /B "" "%ROOT%.venv\Scripts\python.exe" -m http.server 5500 --directory "%ROOT%frontend\public"

REM Open browser
start "" "http://localhost:5500"
echo All services running! Press Ctrl+C to stop.
pause