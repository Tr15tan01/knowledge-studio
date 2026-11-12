<#
.SYNOPSIS
  Install Knowledge Studio dependencies natively (no Docker) and create configuration files.

.DESCRIPTION
  - Checks Python (3.12+, 3.13 recommended) and Node.js (20.9+, 22 LTS recommended)
  - Creates backend/.venv and installs Python packages
  - Installs frontend packages with npm ci
  - Creates .env from .env.example with a freshly generated SESSION_SECRET (never overwrites)
  - Creates frontend/.env.local pointing the web app at the local API
  - With -Production: installs runtime packages only and builds the frontend

.EXAMPLE
  ./scripts/setup.ps1
  ./scripts/setup.ps1 -Production
#>
[CmdletBinding()]
param(
    [switch]$Production,
    [switch]$SkipFrontend,
    [string]$Python = ""
)

. "$PSScriptRoot/common.ps1"

function Find-Python {
    if ($Python) { return , @($Python) }
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += , @("py", "-3.13"); $candidates += , @("py", "-3.12") }
    foreach ($name in @("python3.13", "python3", "python")) {
        if (Get-Command $name -ErrorAction SilentlyContinue) { $candidates += , @($name) }
    }
    foreach ($c in $candidates) {
        $exe = $c[0]; $pre = @(); if ($c.Count -gt 1) { $pre = $c[1..($c.Count - 1)] }
        try {
            $v = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v) {
                $parts = $v.Trim().Split(".")
                if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 12) { return , $c }
            }
        } catch { }
    }
    throw "Python 3.12+ was not found. Install Python 3.13 from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')."
}

Write-Step "Checking prerequisites"
$py = Find-Python
$pyExe = $py[0]; $pyPre = @(); if ($py.Count -gt 1) { $pyPre = $py[1..($py.Count - 1)] }
Write-Host ("Python: " + (& $pyExe @pyPre --version))

if (-not $SkipFrontend) {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw "Node.js was not found. Install Node.js 22 LTS from https://nodejs.org/." }
    $nodeVersion = (node --version).TrimStart("v")
    $major = [int]($nodeVersion.Split(".")[0]); $minor = [int]($nodeVersion.Split(".")[1])
    if ($major -lt 20 -or ($major -eq 20 -and $minor -lt 9)) { throw "Node.js $nodeVersion is too old. Next.js 16 needs 20.9 or newer (22 LTS recommended)." }
    Write-Host "Node.js: v$nodeVersion"
}

Write-Step "Creating Python virtual environment (backend/.venv)"
$venvPy = Get-VenvPython
if (-not (Test-Path $venvPy)) {
    Invoke-Checked -File $pyExe -Arguments ($pyPre + @("-m", "venv", ".venv")) -WorkingDirectory $BackendDir
}
Invoke-Checked -File $venvPy -Arguments @("-m", "pip", "install", "--upgrade", "pip") -WorkingDirectory $BackendDir
$req = if ($Production) { "requirements.txt" } else { "requirements-dev.txt" }
Invoke-Checked -File $venvPy -Arguments @("-m", "pip", "install", "-r", $req) -WorkingDirectory $BackendDir

if (-not $SkipFrontend) {
    Write-Step "Installing frontend packages (npm ci)"
    Invoke-Checked -File "npm" -Arguments @("ci", "--no-audit", "--no-fund") -WorkingDirectory $FrontendDir
}

Write-Step "Configuration files"
$envFile = Join-Path $RepoRoot ".env"
if (Test-Path $envFile) {
    Write-Host ".env already exists; left unchanged."
} else {
    $content = Get-Content (Join-Path $RepoRoot ".env.example") -Raw
    $content = $content -replace "(?m)^SESSION_SECRET=.*$", ("SESSION_SECRET=" + (New-RandomSecret))
    if ($Production) { $content = $content -replace "(?m)^APP_ENV=.*$", "APP_ENV=production" -replace "(?m)^LOG_JSON=.*$", "LOG_JSON=1" }
    [System.IO.File]::WriteAllText($envFile, $content, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Created .env with a new SESSION_SECRET."
}
$webEnv = Join-Path $FrontendDir ".env.local"
if (-not (Test-Path $webEnv)) {
    Copy-Item (Join-Path $FrontendDir ".env.example") $webEnv
    Write-Host "Created frontend/.env.local."
}
New-Item -ItemType Directory -Force -Path (Join-Path $BackendDir "var/storage") | Out-Null

if ($Production -and -not $SkipFrontend) {
    Write-Step "Building the frontend"
    Invoke-Checked -File "npm" -Arguments @("run", "build") -WorkingDirectory $FrontendDir
}

Write-Step "Done"
$missing = @()
$envText = Get-Content $envFile -Raw
if ($envText -match "(?m)^MONGODB_URI=\s*$") { $missing += "MONGODB_URI" }
if ($envText -match "(?m)^GEMINI_API_KEY=\s*$") { $missing += "GEMINI_API_KEY" }
if ($missing.Count -gt 0) {
    Write-Host ("Set these in .env before continuing: " + ($missing -join ", ")) -ForegroundColor Yellow
}
Write-Host "Next: ./scripts/setup-db.ps1, then ./scripts/dev-all.ps1"
