<#
.SYNOPSIS
  Open the API, worker and web app, each in its own terminal window.
  Close a window (or press Ctrl+C in it) to stop that process.
#>
[CmdletBinding()]
param()
. "$PSScriptRoot/common.ps1"
Assert-Venv | Out-Null
Assert-EnvFile
$shell = Get-PowerShellExe

foreach ($name in @("dev-api.ps1", "dev-worker.ps1", "dev-frontend.ps1")) {
    $path = Join-Path $PSScriptRoot $name
    if ($script:IsWin) {
        Start-Process -FilePath $shell -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$path`"") -WorkingDirectory $RepoRoot
    } else {
        # macOS/Linux: run in the background of this terminal; logs go to ./var/logs
        $logs = Join-Path $RepoRoot "var/logs"; New-Item -ItemType Directory -Force -Path $logs | Out-Null
        Start-Process -FilePath $shell -ArgumentList @("-File", $path) -RedirectStandardOutput (Join-Path $logs "$name.log") -RedirectStandardError (Join-Path $logs "$name.err.log")
        Write-Host "Started $name (logs in var/logs)"
    }
}
Write-Host ""
Write-Host "API      http://localhost:8000/api/docs"
Write-Host "Web app  http://localhost:3000"
