param([Parameter(Mandatory=$true)][string]$Action, [string]$ProjectId = '')
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Config = Get-Content -Raw -LiteralPath (Join-Path $Root '.damask-runtime.json') | ConvertFrom-Json
$CommandArgs = @('-m','damask_chief_of_staff.cli',$Action)
if ($ProjectId) { $CommandArgs += $ProjectId }
if ($Config.runtime -eq 'wsl') { & wsl.exe -d $Config.distro -- $Config.python @CommandArgs }
else { & $Config.python @CommandArgs }
if ($LASTEXITCODE -ne 0) { throw "Damask $Action failed." }
