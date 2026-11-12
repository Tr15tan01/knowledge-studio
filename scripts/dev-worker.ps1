<#
.SYNOPSIS
  Run a background worker (ingestion, embeddings, graph extraction, research, deletion, maintenance).
  Stop with Ctrl+C: running jobs reach a checkpoint; anything unfinished is resumed via its lease.
.EXAMPLE
  ./scripts/dev-worker.ps1
  ./scripts/dev-worker.ps1 -Types "ingest.file,graph.extract" -Concurrency 4
#>
[CmdletBinding()]
param([string]$Types = "", [int]$Concurrency = 0)
. "$PSScriptRoot/common.ps1"
$py = Assert-Venv
Assert-EnvFile
$workerArgs = @("-m", "workers.main")
if ($Types) { $workerArgs += @("--types", $Types) }
if ($Concurrency -gt 0) { $workerArgs += @("--concurrency", "$Concurrency") }
Write-Step "Worker"
Invoke-Checked -File $py -Arguments $workerArgs -WorkingDirectory $BackendDir
