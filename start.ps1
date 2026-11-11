$ErrorActionPreference = 'Stop'
$ConfigPath = Join-Path $PSScriptRoot '.damask-runtime.json'
if (-not (Test-Path -LiteralPath $ConfigPath)) { & (Join-Path $PSScriptRoot 'Setup.ps1') -NoStart }
$Config = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
if ($Config.runtime -eq 'wsl') {
    $LaunchUrl = (& wsl.exe -d $Config.distro -- $Config.python -m damask_chief_of_staff.cli browser-url)
    if ($LASTEXITCODE -ne 0) { throw 'Damask failed to start in WSL.' }
    Start-Process $LaunchUrl.Trim()
} else {
    & $Config.python -m damask_chief_of_staff.cli start
    if ($LASTEXITCODE -ne 0) { throw 'Damask failed to start. Run status.ps1 and inspect the private server log.' }
}
