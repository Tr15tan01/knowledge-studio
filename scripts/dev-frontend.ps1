<#
.SYNOPSIS
  Run the Next.js dev server on http://localhost:3000. /api/* is proxied to API_INTERNAL_URL
  (frontend/.env.local, default http://127.0.0.1:8000).
#>
[CmdletBinding()]
param([int]$Port = 3000)
. "$PSScriptRoot/common.ps1"
if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) { throw "Frontend packages missing. Run ./scripts/setup.ps1 first." }
Write-Step "Web app on http://localhost:$Port"
Invoke-Checked -File "npx" -Arguments @("next", "dev", "--port", "$Port") -WorkingDirectory $FrontendDir
