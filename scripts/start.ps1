param([int]$Port = 8000, [string]$Database = 'data/research.sqlite3')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { throw 'Run scripts/setup.ps1 first' }
if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) { throw 'Build frontend first: cd frontend; npm ci; npm run build' }
if ($Port -ne 8000) { throw 'Use port 8000; the browser origin guard is configured for 8000/5173' }
$env:RESEARCH_DB = $Database
& '.venv/Scripts/python.exe' -m uvicorn backend.main:app --host 127.0.0.1 --port $Port
