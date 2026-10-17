# Start the Auditor backend (FastAPI + uvicorn) serving the static frontend.
# Python Scripts may be off PATH on this machine; use `py -m uvicorn`.
# NOTE: the bind address parameter is named -BindHost (not -Host) because $Host
# is a read-only automatic variable in PowerShell and cannot be used as a param.

param(
  [int]$Port = 8000,
  [string]$BindHost = "127.0.0.1"
)

$repo = "N:\Auditor"
Set-Location $repo

Write-Host "Starting Auditor on http://${BindHost}:$Port" -ForegroundColor Cyan
Write-Host "Backend: FastAPI; serving / from $repo\frontend" -ForegroundColor Gray
Write-Host "Press Ctrl+C to stop." -ForegroundColor Gray

py -m uvicorn backend.main:app --host $BindHost --port $Port --reload 2>&1 | ForEach-Object { $_ }
