<#
.SYNOPSIS
  Run the automated tests.
.DESCRIPTION
  Backend tests need a MongoDB replica set: set TEST_MONGODB_URI (Atlas test cluster or a local
  `mongod --replSet rs0`); default mongodb://127.0.0.1:27017/?replicaSet=rs0.
  -E2E additionally runs Playwright (builds the frontend, starts API + worker in test mode).
.EXAMPLE
  ./scripts/test.ps1
  ./scripts/test.ps1 -E2E
#>
[CmdletBinding()]
param([switch]$E2E, [switch]$SkipBackend, [switch]$SkipFrontend)
. "$PSScriptRoot/common.ps1"

if (-not $SkipBackend) {
    $py = Assert-Venv
    Write-Step "Backend tests (pytest)"
    Invoke-Checked -File $py -Arguments @("-m", "pytest", "-q") -WorkingDirectory $BackendDir
}
if (-not $SkipFrontend) {
    Write-Step "Frontend unit tests (vitest)"
    Invoke-Checked -File "npm" -Arguments @("test") -WorkingDirectory $FrontendDir
}
if ($E2E) {
    Write-Step "End-to-end tests (Playwright)"
    if (-not $env:E2E_MONGODB_URI -and $env:TEST_MONGODB_URI) { $env:E2E_MONGODB_URI = $env:TEST_MONGODB_URI }
    Invoke-Checked -File "npx" -Arguments @("playwright", "install", "chromium") -WorkingDirectory $FrontendDir
    Invoke-Checked -File "npm" -Arguments @("run", "e2e") -WorkingDirectory $FrontendDir
}
Write-Host "All requested test suites passed." -ForegroundColor Green
