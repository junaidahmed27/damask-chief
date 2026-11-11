param([Parameter(Mandatory=$true)][string]$ProjectId)
& (Join-Path $PSScriptRoot 'scripts/invoke.ps1') -Action disconnect -ProjectId $ProjectId
