# ChatSphere

Real-time chat application (FastAPI backend, Node SSE gateway, static frontend).

Quick start (local):

1. Create and activate Python venv, install deps:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Start services (or use the provided script):

```powershell
# from repo root
powershell -ExecutionPolicy Bypass -File .\start-all.ps1
```

Services:
- Python API: http://localhost:8000 (OpenAPI: /docs)
- Node SSE: http://localhost:3001
- Frontend: http://localhost:5500

Files of interest:
- `backend-python/main.py`
- `backend-node/server.js`
- `frontend/public/index.html`

License: MIT