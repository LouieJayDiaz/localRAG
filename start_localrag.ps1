$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pythonLauncher) {
    $python = "py"
    $pythonArguments = @("-3")
} else {
    $python = "python"
    $pythonArguments = @()
}

& $python @pythonArguments -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.11 or newer is required. Install Python, then run this script again."
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    & $python @pythonArguments -m venv (Join-Path $PSScriptRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Could not create the LocalRAG virtual environment." }
}

& $venvPython -m pip install -e ".[documents,images,web]"
if ($LASTEXITCODE -ne 0) { throw "Could not install LocalRAG dependencies." }
& $venvPython -m localrag.web
exit $LASTEXITCODE
