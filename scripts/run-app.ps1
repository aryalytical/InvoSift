$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root 'backend_src'
$Venv = Join-Path $Root '.run-venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'

if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js is required. Install the Node.js LTS version, then reopen this file.' }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Python 3.12 x64 is required for the source-run version. Install Python, then reopen this file.' }

# Install the JS dependency tree without relying on npm's optional install-script allowlist.
# Electron's official postinstall is then invoked directly, which avoids the "electron not recognized"
# failure seen with npm versions that block lifecycle scripts by default.
$ElectronCmd = Join-Path $Root 'node_modules\.bin\electron.cmd'
$ElectronExe = Join-Path $Root 'node_modules\electron\dist\electron.exe'
if (-not (Test-Path $ElectronExe)) {
    Write-Host 'Preparing Electron runtime (first launch only)...' -ForegroundColor Cyan
    Push-Location $Root
    try {
        if (-not (Test-Path (Join-Path $Root 'node_modules\electron\package.json'))) {
            & npm install --include=dev --ignore-scripts --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw "npm dependency installation failed (exit $LASTEXITCODE). Check network access and retry." }
        }
        $ElectronInstaller = Join-Path $Root 'node_modules\electron\install.js'
        if (-not (Test-Path $ElectronInstaller)) { throw 'Electron package is missing. Delete node_modules and run npm install --include=dev, then retry.' }
        & node $ElectronInstaller
        if ($LASTEXITCODE -ne 0) { throw "Electron runtime download failed (exit $LASTEXITCODE). Check access to GitHub release downloads and retry RUN-APP.bat." }
    } finally { Pop-Location }
}
if (-not (Test-Path $ElectronExe)) { throw 'Electron runtime is still missing after setup. Check your internet connection and retry.' }
if (-not (Test-Path $VenvPython)) {
    Write-Host 'First-time setup: creating a private Python environment...' -ForegroundColor Cyan
    python -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
$NeedInstall = -not (Test-Path (Join-Path $Venv 'invoice-ai-deps-ready.txt'))
if ($NeedInstall) {
    Write-Host 'First-time setup: installing invoice/OCR dependencies. This may take several minutes and needs internet access...' -ForegroundColor Cyan
    & $VenvPython -m pip install --disable-pip-version-check --retries 10 --timeout 120 -r (Join-Path $Backend 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your internet connection and run RUN-APP.bat again; the setup will retry.' }
    Set-Content -Path (Join-Path $Venv 'invoice-ai-deps-ready.txt') -Value 'ready'
}
Write-Host 'Launching InvoSift desktop window. Close the window to stop the app.' -ForegroundColor Green
$env:PYTHON = $VenvPython
Push-Location $Root
try { & $ElectronCmd .; if ($LASTEXITCODE -ne 0) { throw "Electron did not start (exit $LASTEXITCODE). Check the Electron runtime installation and network access." } }
finally { Pop-Location }
