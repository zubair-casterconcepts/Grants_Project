# Monday weekly grant digest → n8n webhook.
# Schedule with Windows Task Scheduler (every Monday morning), e.g.:
#   Program: powershell.exe
#   Args:    -File "C:\path\to\Grants\scripts\send_weekly_digests.ps1"
#
# Or let n8n own the schedule: Schedule Trigger → POST /accounts/digest/run/
# with header X-Webhook-Token: <N8N_WEBHOOK_AUTH_HEADER_VALUE>

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Virtualenv not found at $Python"
}

& $Python manage.py send_weekly_digests @args
exit $LASTEXITCODE
