param([ValidateSet('Native','WSL')][string]$Runtime = 'Native', [string]$Distro = '', [switch]$NoStart)
$ErrorActionPreference = 'Stop'
$RepoDir = $PSScriptRoot
Set-Location -LiteralPath $RepoDir
if ($Runtime -eq 'WSL') {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { throw 'WSL is not installed. Use Native; Damask does not install WSL.' }
    if (-not $Distro) { throw 'Choose an existing development distribution with -Distro. Run wsl -l -v to list it.' }
    if ($Distro -match 'docker|system') { throw 'Select a development distribution, not a Docker/system distribution.' }
    & wsl.exe -d $Distro -- true
    if ($LASTEXITCODE -ne 0) { throw 'The selected WSL distribution is not usable.' }
    $Kernel = (& wsl.exe -d $Distro -- uname -r)
    if ($Kernel -notmatch 'WSL2') { throw 'Select an existing WSL2 distribution, or use Native.' }
    $LinuxRepo = (& wsl.exe -d $Distro -- wslpath -u $RepoDir).Trim()
    & wsl.exe -d $Distro -- bash "$LinuxRepo/scripts/setup.sh" --no-start
    if ($LASTEXITCODE -ne 0) { throw 'WSL setup failed.' }
    @{ runtime='wsl'; distro=$Distro; python="$LinuxRepo/.venv-wsl/bin/python" } | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath '.damask-runtime.json'
} else {
    $PythonCommand = $null
    $PythonArgs = @()
    foreach ($candidate in @('py','python3','python')) {
        if (-not (Get-Command $candidate -ErrorAction SilentlyContinue)) { continue }
        $candidateArgs = @()
        if ($candidate -eq 'py') { $candidateArgs = @('-3') }
        & $candidate @candidateArgs -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) { $PythonCommand=$candidate; $PythonArgs=$candidateArgs; break }
    }
    if (-not $PythonCommand) { throw 'Install Python 3.12+ for this user, then double-click Start.cmd. No WSL is required.' }
    $VenvPython = Join-Path $RepoDir '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        & $PythonCommand @PythonArgs -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated Python environment.' }
    }
    & $VenvPython -m pip install --require-hashes -r requirements.lock
    if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed.' }
    & $VenvPython -m pip install --no-deps .
    if ($LASTEXITCODE -ne 0) { throw 'Damask installation failed.' }
    & $VenvPython -m damask_chief_of_staff.cli init
    if ($LASTEXITCODE -ne 0) { throw 'Private state initialization failed.' }
    @{ runtime='native'; python=$VenvPython } | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath '.damask-runtime.json'
}
if (-not $NoStart) { & (Join-Path $RepoDir 'start.ps1') }
