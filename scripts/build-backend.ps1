$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend_src"
$BackendPackage = Join-Path $Backend "backend"
$BackendEntry = Join-Path $Backend "backend_entry.py"
$Venv = Join-Path $Root ".build-venv"

$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonCommand) {
    throw "Python 3.12 x64 is required. Install Python 3.12 x64 and enable Add Python to PATH."
}
$Python = $PythonCommand.Source
$PythonInfo = & $Python -c "import sys,struct; print(str(sys.version_info.major)+'.'+str(sys.version_info.minor)+';'+str(struct.calcsize(chr(80))*8))"
if ($LASTEXITCODE -ne 0 -or $PythonInfo -notmatch '^3\.12;64$') {
    throw "The first Python on PATH must be 64-bit Python 3.12. Detected: $PythonInfo"
}

Write-Host "Preparing Python 3.12 x64 build environment..." -ForegroundColor Cyan
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
    & $Python -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the build virtual environment (exit $LASTEXITCODE)." }
}
$VenvPython = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) { throw "Build Python was not created at $VenvPython." }

if (-not (Test-Path -LiteralPath $Backend)) { throw "Backend source directory was not found: $Backend" }
if (-not (Test-Path -LiteralPath $BackendPackage -PathType Container)) { throw "Backend Python package directory was not found: $BackendPackage" }
if (-not (Test-Path -LiteralPath $BackendEntry -PathType Leaf)) { throw "Backend entry point was not found: $BackendEntry" }

Push-Location $Backend
try {
    Write-Host "Installing backend and PyInstaller dependencies..." -ForegroundColor Cyan
    & $VenvPython -m pip install --disable-pip-version-check --retries 10 --timeout 180 --prefer-binary -r requirements.txt pyinstaller
    if ($LASTEXITCODE -ne 0) {
        throw "Python dependencies could not be installed. Check the network connection and rerun BUILD-WINDOWS.bat. No new release has been produced."
    }

    Write-Host "Bundling FastAPI, PaddleOCR, PaddlePaddle and document-processing dependencies..." -ForegroundColor Cyan
    $Dist = Join-Path $Backend "dist"
    $Work = Join-Path $Backend "build"
    $Spec = Join-Path $Backend "spec"
    New-Item -ItemType Directory -Path $Spec -Force | Out-Null
    & $VenvPython -m PyInstaller --noconfirm --clean --onedir --name invoice-backend `
      --distpath $Dist --workpath $Work --specpath $Spec `
      --collect-all paddleocr --collect-all paddlex --collect-all paddle `
      --collect-all pypdfium2 --collect-all cv2 --collect-all PIL `
      --collect-all openpyxl --collect-all pandas `
      --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto `
      --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.lifespan.on `
      --hidden-import backend.routes.chat --hidden-import backend.routes.invoices `
      --hidden-import backend.routes.health --hidden-import backend.services.ocr_service `
      --hidden-import backend.services.ai_extractor --hidden-import backend.services.invoice_processor `
      --hidden-import backend.services.deterministic_extractor --hidden-import backend.services.export_service `
      --hidden-import backend.database --hidden-import backend.models.invoice `
      --hidden-import backend.schemas.invoice_schema --hidden-import backend.config `
      --hidden-import backend.utils.image_utils --hidden-import backend.utils.file_utils `
      --hidden-import backend.services.validation_service --collect-submodules backend `
      --collect-submodules paddleocr --collect-submodules paddlex `
      --add-data "$BackendPackage;backend" $BackendEntry
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE). See the first PyInstaller error above." }

    $Built = Join-Path $Dist "invoice-backend"
    $Exe = Join-Path $Built "invoice-backend.exe"
    if (-not (Test-Path $Exe)) { throw "PyInstaller returned success, but invoice-backend.exe was not found at $Exe." }

    $Out = Join-Path $Root "backend_dist"
    if (Test-Path $Out) { Remove-Item $Out -Recurse -Force }
    New-Item -ItemType Directory -Path $Out | Out-Null
    Copy-Item (Join-Path $Built "*") $Out -Recurse -Force
    if (-not (Test-Path (Join-Path $Out "invoice-backend.exe"))) {
        throw "Backend files were not copied correctly into backend_dist."
    }
} finally {
    Pop-Location
}
Write-Host "Backend bundle created successfully in backend_dist." -ForegroundColor Green
