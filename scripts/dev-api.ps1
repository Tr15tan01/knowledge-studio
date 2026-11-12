<#
.SYNOPSIS
  Run the FastAPI server with auto-reload on http://localhost:8000 (OpenAPI docs at /api/docs).
#>
[CmdletBinding()]
param([int]$Port = 8000)
. "$PSScriptRoot/common.ps1"
$py = Assert-Venv
Assert-EnvFile
Write-Step "API on http://localhost:$Port"
# --proxy-headers lets rate limiting see the real client IP behind the Next.js dev proxy.
Invoke-Checked -File $py -Arguments @("-m", "uvicorn", "app.main:app", "--reload", "--host", "127.0.0.1", "--port", "$Port", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1") -WorkingDirectory $BackendDir
