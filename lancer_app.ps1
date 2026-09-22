$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

& ".venv\Scripts\python.exe" -m pip install -r requirements.txt
& ".venv\Scripts\python.exe" scripts\prepare_data.py

Write-Host ""
Write-Host "Application disponible sur http://127.0.0.1:8050"
Write-Host "Utilisez Ctrl+C pour l'arrêter."
& ".venv\Scripts\python.exe" app.py

