$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Python environment not found. Follow backend/README.md to create it and install requirements.'
}
$env:PYTHONPATH = Join-Path $projectRoot 'backend\src'
Push-Location $projectRoot
try {
    & $python -m vishing.main
} finally {
    Pop-Location
}
