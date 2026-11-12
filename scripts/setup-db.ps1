<#
.SYNOPSIS
  Create collections, validators and indexes, seed the plan catalog, and create Atlas search indexes.
  Safe to run repeatedly (idempotent). Run once per release.
#>
[CmdletBinding()]
param(
    [switch]$SkipSearchIndexes,
    [switch]$NoWait
)
. "$PSScriptRoot/common.ps1"
$py = Assert-Venv
Assert-EnvFile

Write-Step "Configuration check"
Invoke-Checked -File $py -Arguments @("-m", "app.cli", "check-config") -WorkingDirectory $BackendDir

Write-Step "Collections, validators, indexes and plan catalog"
Invoke-Checked -File $py -Arguments @("-m", "app.cli", "setup-db") -WorkingDirectory $BackendDir

if (-not $SkipSearchIndexes) {
    Write-Step "Atlas Vector Search and Atlas Search indexes"
    $searchArgs = @("-m", "app.cli", "setup-search-indexes")
    if (-not $NoWait) { $searchArgs += "--wait" }
    try { Invoke-Checked -File $py -Arguments $searchArgs -WorkingDirectory $BackendDir }
    catch {
        Write-Host "Search index setup failed. Atlas indexes need an Atlas cluster (M0 works)." -ForegroundColor Yellow
        Write-Host "For a local mongod, rerun with -SkipSearchIndexes and set VECTOR_SEARCH_BACKEND=exact and KEYWORD_SEARCH_BACKEND=text_index (development only)." -ForegroundColor Yellow
        throw
    }
}
Write-Host "Database ready."
