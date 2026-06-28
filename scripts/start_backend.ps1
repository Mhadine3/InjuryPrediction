# start_backend.ps1
# Run from repo root: .\scripts\start_backend.ps1
Set-Location "$PSScriptRoot\.."
$env:PYTHONPATH = "backend"
uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000 --reload
