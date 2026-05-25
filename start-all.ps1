Param(
    [switch]$NoBrowser
)

$root = Split-Path -Parent $MyInvocation.MyCommand.Definition

Write-Output "Starting ChatSphere services from: $root"

# --- Ensure venv and Python deps ---
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Output ".venv not found — creating virtual environment and installing requirements..."
    & python -m venv "$root\.venv"
    & "$root\.venv\Scripts\python.exe" -m pip install --upgrade pip
    & "$root\.venv\Scripts\python.exe" -m pip install -r "$root\requirements.txt"
}

# --- Start Python FastAPI (uvicorn) ---
Write-Output "Starting Python backend (uvicorn) on port 8000..."
Start-Process -FilePath $venvPython -ArgumentList "-m uvicorn main:app --reload --host 0.0.0.0 --port 8000" -WorkingDirectory (Join-Path $root "backend-python") -NoNewWindow

# --- Start Node SSE service ---
Write-Output "Starting Node SSE service on port 3001..."
Start-Process -FilePath "cmd.exe" -ArgumentList "/c npm start" -WorkingDirectory (Join-Path $root "backend-node") -NoNewWindow

# --- Serve frontend static files ---
Write-Output "Serving frontend at http://localhost:5500..."
Start-Process -FilePath $venvPython -ArgumentList "-m http.server 5500" -WorkingDirectory (Join-Path $root "frontend\public") -NoNewWindow

# Give services a moment to appear
Start-Sleep -Seconds 1

if (-not $NoBrowser) {
    try { Start-Process "http://localhost:5500" } catch { }
}

Write-Output "Start commands issued. Check the spawned terminals for logs." 
