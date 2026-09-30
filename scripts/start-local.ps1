$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
# Set BLASTIO_* environment variables in this PowerShell session before starting.
& .\.venv\Scripts\python.exe -m uvicorn blastio.app:app --host 127.0.0.1 --port 8000 --workers 1
exit $LASTEXITCODE
