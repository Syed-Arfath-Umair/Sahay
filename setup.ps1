# Sahay setup for Windows 11 - Snapdragon (ARM64) first, also works on x64 (Intel/AMD).
# Run in PowerShell:   powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

function Say($t, $c = "Gray") { Write-Host $t -ForegroundColor $c }

# ---- 1. which machine is this?
$native = $env:PROCESSOR_ARCHITECTURE
if ($env:PROCESSOR_ARCHITEW6432) { $native = $env:PROCESSOR_ARCHITEW6432 }
try {
    $cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name)
} catch { $cpu = "unknown" }
Say ""
Say "== Sahay setup ==" Cyan
Say "Processor: $cpu  ($native)"
$isArm = ($native -eq "ARM64")
if ($isArm) { Say "Snapdragon / Windows on ARM detected - setting up NATIVE ARM64 (no emulation)." Green }

# ---- 2. find Python (prefer native ARM64 builds on Snapdragon)
$candidates = @()
if ($isArm) {
    $candidates += ,@("py", "-V:3.12-arm64"); $candidates += ,@("py", "-V:3.13-arm64"); $candidates += ,@("py", "-V:3.11-arm64")
    $candidates += ,@("$env:LOCALAPPDATA\Programs\Python\Python312-arm64\python.exe")
    $candidates += ,@("$env:LOCALAPPDATA\Programs\Python\Python313-arm64\python.exe")
}
$candidates += ,@("py", "-3.12"); $candidates += ,@("py", "-3.13"); $candidates += ,@("py", "-3.11"); $candidates += ,@("py", "-3")
$candidates += ,@("python"); $candidates += ,@("python3")
$candidates += ,@("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe")
$candidates += ,@("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe")

$pyCmd = $null; $pyArch = ""
foreach ($c in $candidates) {
    $exe = $c[0]; $a = @(); if ($c.Count -gt 1) { $a = $c[1..($c.Count - 1)] }
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue) -and -not (Test-Path $exe)) { continue }
    $out = & $exe @a -c "import sys,platform; print(sys.version_info[0]*100+sys.version_info[1], platform.machine())" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $out) { continue }
    $parts = "$out".Trim().Split(" ")
    if ($parts[0] -match '^\d+$' -and [int]$parts[0] -ge 311) {
        if ($isArm -and $parts[1] -ne "ARM64") {
            if (-not $pyCmd) { $pyCmd = $c; $pyArch = $parts[1] }   # remember x64 as last resort
            continue
        }
        $pyCmd = $c; $pyArch = $parts[1]; break
    }
}
if (-not $pyCmd) {
    Say ""
    Say "No working Python 3.11+ found." Red
    Say "Install Python 3.12 from https://www.python.org/downloads/windows/"
    Say "  - Snapdragon PCs: 'Windows installer (ARM64)'"
    Say "  - Intel/AMD PCs:  'Windows installer (64-bit)'"
    Say "  - Tick 'Add python.exe to PATH', then open a NEW PowerShell and run setup.ps1 again."
    exit 1
}
if ($isArm -and $pyArch -ne "ARM64") {
    Say ""
    Say "WARNING: only x64 Python found. It runs under emulation (slower, and the Snapdragon NPU runtime" Yellow
    Say "cannot load). For the best experience install the ARM64 Python 3.12 build and re-run setup." Yellow
    Say ""
}
$pyExe = $pyCmd[0]; $pyArgs = @(); if ($pyCmd.Count -gt 1) { $pyArgs = $pyCmd[1..($pyCmd.Count - 1)] }
Say "Using Python: $($pyCmd -join ' ')  [$pyArch]" Green

# ---- 3. virtual environment (recreate if its architecture doesn't match)
$venvPy = ".\.venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $va = & $venvPy -c "import platform; print(platform.machine())" 2>$null
    if ("$va".Trim() -ne $pyArch) { Say "Recreating .venv for $pyArch ..." Yellow; Remove-Item -Recurse -Force .venv }
}
if (-not (Test-Path $venvPy)) { & $pyExe @pyArgs -m venv .venv }
if (-not (Test-Path $venvPy)) { Say "Could not create the virtual environment (.venv)." Red; exit 1 }

# self-heal a broken pip (e.g. an interrupted earlier install)
& $venvPy -m pip --version *> $null
if ($LASTEXITCODE -ne 0) {
    Say "pip in .venv is broken - repairing..." Yellow
    & $venvPy -m ensurepip --upgrade *> $null
    & $venvPy -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) {
        Say "Rebuilding .venv from scratch..." Yellow
        Remove-Item -Recurse -Force .venv
        & $pyExe @pyArgs -m venv .venv
    }
}
& $venvPy -m pip --version *> $null
if ($LASTEXITCODE -ne 0) { Say "pip is not working in .venv. Delete the .venv folder and run setup again." Red; exit 1 }

# Upgrading pip is optional; never let it break the install.
& $venvPy -m pip install --upgrade pip --quiet --disable-pip-version-check *> $null
& $venvPy -m pip --version *> $null
if ($LASTEXITCODE -ne 0) { & $venvPy -m ensurepip --upgrade *> $null }

Say "Installing core packages..." Cyan
& $venvPy -m pip install -r requirements.txt --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Say "Core packages failed to install - see errors above." Red; exit 1 }

function Install-Optional($label, $pkgs) {
    Say "Installing $label ..." Cyan
    & $venvPy -m pip install @pkgs --quiet
    if ($LASTEXITCODE -ne 0) { Say "  (skipped: $label isn't available for this Python/CPU - Sahay still works)" Yellow }
}
Install-Optional "on-device speech recognition (sherpa-onnx Whisper)" @("sherpa-onnx")
Install-Optional "neural voices (edge-tts)" @("edge-tts")
Install-Optional "Windows on-device OCR" @("winrt-runtime", "winrt-Windows.Media.Ocr", "winrt-Windows.Graphics.Imaging",
    "winrt-Windows.Storage.Streams", "winrt-Windows.Foundation", "winrt-Windows.Foundation.Collections",
    "winrt-Windows.Globalization")
if ($pyArch -eq "ARM64") {
    Install-Optional "ONNX Runtime QNN (Snapdragon Hexagon NPU)" @("onnxruntime-qnn")
} else {
    Install-Optional "ONNX Runtime DirectML (GPU)" @("onnxruntime-directml")
}

# ---- 4. speech model (one time)
Say "Downloading the on-device speech model (one time, ~100 MB)..." Cyan
& $venvPy -c "from sahay.voice.stt import ensure_sherpa_model; ensure_sherpa_model('base.en'); print('Speech model ready')"

# ---- 5. on-device language model
Say ""
Say "On-device AI:" Cyan
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    $models = (ollama list) -join "`n"
    if ($models -notmatch "qwen2.5vl:3b") { Say "Pulling qwen2.5vl:3b (about 3 GB)..."; ollama pull qwen2.5vl:3b }
    else { Say "qwen2.5vl:3b already installed." Green }
} else {
    Say "Install Ollama (native ARM64 build) from https://ollama.com/download, then run:  ollama pull qwen2.5vl:3b"
}
if ($isArm) {
    Say ""
    Say "Snapdragon NPU (recommended): install Microsoft Foundry Local and start an NPU model:" Cyan
    Say "    winget install Microsoft.FoundryLocal"
    Say "    foundry model run phi-4-mini"
    Say "Then in Sahay: Settings > On-device AI > 'Use Snapdragon NPU'."
}

# ---- 6. health check
Say ""
& $venvPy -m sahay.doctor
Say "Done. Start Sahay with run.bat  (hotkey: Ctrl+Alt+Space, Esc to cancel)" Green
