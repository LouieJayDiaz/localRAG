$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

$python = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Create .venv first, or run start_localrag.ps1 once."
}

& $python -m pip install -e ".[build,desktop,documents,images,web]"
if ($LASTEXITCODE -ne 0) { throw "Could not install desktop build dependencies." }

& $python -m PyInstaller --clean --noconfirm packaging\localrag.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller desktop build failed." }

Write-Host "Desktop build written to dist\LocalRAG"
