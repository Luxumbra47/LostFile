# Run LostFile in dev mode (uses project-local .venv; data stays in project data/)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Virtualenv not found. Create it first: py -3.12 -m venv .venv ; .venv\Scripts\python.exe -m pip install -r requirements.txt" -ForegroundColor Red
    exit 1
}
Push-Location $root
try { & $py -m lostfile } finally { Pop-Location }