<#
.SYNOPSIS
  Static checks: lint, formatting, type checks and dependency vulnerability audits.
#>
[CmdletBinding()]
param()
. "$PSScriptRoot/common.ps1"
$py = Assert-Venv

Write-Step "Backend: ruff lint + format check"
Invoke-Checked -File $py -Arguments @("-m", "ruff", "check", ".") -WorkingDirectory $BackendDir
Invoke-Checked -File $py -Arguments @("-m", "ruff", "format", "--check", ".") -WorkingDirectory $BackendDir
Write-Step "Backend: mypy"
Invoke-Checked -File $py -Arguments @("-m", "mypy", "app") -WorkingDirectory $BackendDir
Write-Step "Backend: pip-audit"
Invoke-Checked -File $py -Arguments @("-m", "pip_audit", "-r", "requirements.txt") -WorkingDirectory $BackendDir

Write-Step "Frontend: eslint, tsc, npm audit"
Invoke-Checked -File "npm" -Arguments @("run", "lint") -WorkingDirectory $FrontendDir
Invoke-Checked -File "npm" -Arguments @("run", "typecheck") -WorkingDirectory $FrontendDir
Invoke-Checked -File "npm" -Arguments @("audit", "--omit=dev") -WorkingDirectory $FrontendDir
Write-Host "All checks passed." -ForegroundColor Green
