# Shared helpers for Knowledge Studio scripts. Dot-source: . "$PSScriptRoot/common.ps1"
# Works in Windows PowerShell 5.1 and PowerShell 7+ (Windows, macOS, Linux).

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 3.0

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:BackendDir = Join-Path $RepoRoot "backend"
$script:FrontendDir = Join-Path $RepoRoot "frontend"
$script:IsWin = ($PSVersionTable.PSVersion.Major -lt 6) -or $IsWindows

function Get-VenvPython {
    if ($script:IsWin) { return Join-Path $BackendDir ".venv\Scripts\python.exe" }
    return Join-Path $BackendDir ".venv/bin/python"
}

function Assert-Venv {
    $py = Get-VenvPython
    if (-not (Test-Path $py)) {
        throw "Python virtual environment not found at $py. Run ./scripts/setup.ps1 first."
    }
    return $py
}

function Assert-EnvFile {
    if (-not (Test-Path (Join-Path $RepoRoot ".env"))) {
        throw "No .env file in $RepoRoot. Run ./scripts/setup.ps1, then set MONGODB_URI and GEMINI_API_KEY."
    }
}

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-Checked {
    # Run a native command and stop if it fails (PowerShell 5.1 doesn't do this for native exes).
    param([Parameter(Mandatory)][string]$File, [string[]]$Arguments = @(), [string]$WorkingDirectory = $RepoRoot)
    Push-Location $WorkingDirectory
    try {
        & $File @Arguments
        if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $File $($Arguments -join ' ')" }
    }
    finally { Pop-Location }
}

function Get-PowerShellExe {
    # The executable of the current shell, used to open new terminals.
    return (Get-Process -Id $PID).Path
}

function New-RandomSecret([int]$Bytes = 48) {
    $buf = New-Object byte[] $Bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($buf)
    $rng.Dispose()
    return ([Convert]::ToBase64String($buf)).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}
