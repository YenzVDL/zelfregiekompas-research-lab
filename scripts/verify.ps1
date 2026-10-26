$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
& '.venv/Scripts/python.exe' -m pytest backend/tests -q
if ($LASTEXITCODE -ne 0) { throw 'Backend checks failed' }
Push-Location frontend
try {
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    & npm.cmd run test:e2e
    if ($LASTEXITCODE -ne 0) { throw 'Browser checks failed' }
} finally { Pop-Location }
