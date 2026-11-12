<#
.SYNOPSIS
  Promote an existing, registered account to ADMIN or SUPER_ADMIN. Never creates accounts or prints secrets.
.EXAMPLE
  ./scripts/create-admin.ps1 -Email you@example.com -Role SUPER_ADMIN
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Email,
    [ValidateSet("ADMIN", "SUPER_ADMIN")][string]$Role = "ADMIN",
    [string]$Reason = "bootstrap",
    [switch]$AllowUnverified
)
. "$PSScriptRoot/common.ps1"
$py = Assert-Venv
Assert-EnvFile
$cliArgs = @("-m", "app.cli", "create-admin", "--email", $Email, "--role", $Role, "--reason", $Reason)
if ($AllowUnverified) { $cliArgs += "--allow-unverified" }
# The CLI asks you to retype the email to confirm.
Invoke-Checked -File $py -Arguments $cliArgs -WorkingDirectory $BackendDir
