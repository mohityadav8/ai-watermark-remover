<#
.SYNOPSIS
  Windows port of setup_ctrlregen.sh.

.DESCRIPTION
  Bootstraps an external noai-watermark checkout for the optional CtrlRegen
  pixel-domain removal backend.

  The upstream project (https://github.com/mertizci/noai-watermark) does not
  ship a LICENSE file, so its code is treated as all-rights-reserved and is NOT
  bundled in this repository. This script clones it locally and installs only
  the dependencies that clean_ctrlregen.py needs.

  Differences from the .sh version: the Windows venv lives in .venv\Scripts\
  instead of .venv/bin/.

  The torch index is not derived from the driver's CUDA version: the published
  indices are probed (the highest one <= the driver's CUDA that answers HTTP
  200), and torch AND torchvision are installed together from that index so
  installing requirements-ctrlregen.txt cannot replace them with CPU builds
  from PyPI. If a GPU is detected but the final torch has no CUDA support, the
  script warns loudly and exits with a non-zero exit code (never "Done" with a
  silent CPU-only environment).

.PARAMETER Dir
  Checkout directory (default: $env:NOAI_WATERMARK_DIR or ~\noai-watermark)

.PARAMETER Ref
  Commit to use (default: the pinned SHA; do not point at a moving branch)

.PARAMETER Python
  Interpreter used to create the venv (default: python)
#>
[CmdletBinding()]
param(
    [string]$Dir,
    [string]$Ref = 'b642ae45d20eded52c96d570985eb4e3e427aac8',
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Invoke-Checked {
    param([string]$What, [scriptblock]$Block)
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

# Runs a native command discarding its stderr safely.
# In Windows PowerShell, redirecting a native executable's stderr (2>$null,
# 2>&1, 2>file) wraps each line in an ErrorRecord; with
# $ErrorActionPreference = 'Stop' the first one aborts the script. Lower EAP to
# 'Continue' for just this block (e.g. torch warnings on stderr) and restore it
# afterwards.
function Invoke-NativeQuiet {
    param([scriptblock]$Block)
    $prev = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Block 2>$null
    } finally {
        $ErrorActionPreference = $prev
    }
}

if (-not $Dir) {
    if ($env:NOAI_WATERMARK_DIR) { $Dir = $env:NOAI_WATERMARK_DIR }
    else { $Dir = Join-Path $HOME 'noai-watermark' }
}
$parent = Split-Path -Parent $Dir
if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Force -Path $Dir | Out-Null }
$Dir = (Resolve-Path $Dir).Path

if (-not (Test-Path (Join-Path $Dir '.git'))) {
    Write-Host "Cloning noai-watermark into $Dir (pinned ref: $Ref)"
    Invoke-Checked 'git clone' { git clone --depth 1 --filter=blob:none --sparse https://github.com/mertizci/noai-watermark.git $Dir }
    Invoke-Checked 'git fetch' { git -C $Dir fetch --depth 1 origin $Ref }
    Invoke-Checked 'git checkout' { git -C $Dir checkout --detach $Ref }
    Invoke-Checked 'sparse-checkout' { git -C $Dir sparse-checkout set --no-cone '/src/' }
    $head = (git -C $Dir rev-parse HEAD).Trim()
    if ($head -ne $Ref) { throw "error: expected pinned ref $Ref, got $head" }
} else {
    Write-Host "Using existing checkout: $Dir"
    $head = ''
    try { $head = "$(git -C $Dir rev-parse HEAD)".Trim() } catch { $head = '' }
    if ($head -ne $Ref) {
        Write-Host "existing checkout not at pinned ref $Ref (HEAD: $head); re-pinning"
        Invoke-Checked 'git fetch' { git -C $Dir fetch --depth 1 origin $Ref }
        Invoke-Checked 'git checkout' { git -C $Dir checkout --detach $Ref }
        Invoke-Checked 'sparse-checkout' { git -C $Dir sparse-checkout set --no-cone '/src/' }
        $head = (git -C $Dir rev-parse HEAD).Trim()
        if ($head -ne $Ref) { throw "error: expected pinned ref $Ref, got $head" }
    }
}

$venvPython = Join-Path $Dir '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating venv at $Dir\.venv"
    Invoke-Checked 'create venv' { & $Python -m venv (Join-Path $Dir '.venv') }
}

Write-Host 'Installing Python dependencies'
# Pinned pip (an unpinned --upgrade pip was a supply-chain drift point).
Invoke-Checked 'pip install pip' { & $venvPython -m pip install --upgrade 'pip==26.2.1' }

# Install torch from the correct platform index before the remaining pins.
#
# NOTE: nvidia-smi reports the MAXIMUM CUDA the driver supports, not the CUDA
# to install; the driver is backward compatible, so a cu126 wheel runs fine on
# a 13.0 driver. Deriving the wheel tag from that number - as the .sh version
# does - is wrong in general: there is no index per driver version (e.g. a 13.1
# driver would yield cu131, which does not exist, HTTP 403, and the script
# would fall back to the default torch, which is the CPU build on Windows).
# Instead the published indices are probed and the highest one <= the driver's
# CUDA is chosen. Also, cu128+ / CUDA 13 wheels dropped Maxwell/Pascal/Volta
# kernels, so on a Pascal (sm_61) they would install a torch without kernels
# for the card, failing at runtime with "no kernel image is available for
# execution". That is why the compute capability is used, not the driver
# version.
$knownTags = @(
    @{ tag = 'cu130'; version = [version]'13.0' },
    @{ tag = 'cu129'; version = [version]'12.9' },
    @{ tag = 'cu128'; version = [version]'12.8' },
    @{ tag = 'cu126'; version = [version]'12.6' },
    @{ tag = 'cu124'; version = [version]'12.4' },
    @{ tag = 'cu121'; version = [version]'12.1' },
    @{ tag = 'cu118'; version = [version]'11.8' }
)

# Picks the wheel index: the highest one <= the driver's CUDA answering 2xx.
# cc < 7.5 forces cu126 (last index with Maxwell/Pascal/Volta kernels).
function Select-TorchTag([double]$cc, [string]$driverCuda) {
    if ($cc -and $cc -lt 7.5) { return 'cu126' }
    if ($driverCuda -notmatch '^([0-9]+)\.([0-9]+)$') { return $null }
    $driverVersion = [version]"$($Matches[1]).$($Matches[2])"
    foreach ($c in $knownTags) {
        if ($c.version -gt $driverVersion) { continue }
        try {
            $resp = Invoke-WebRequest -UseBasicParsing -Method Head -Uri "https://download.pytorch.org/whl/$($c.tag)" -TimeoutSec 15 -ErrorAction Stop
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300) { return $c.tag }
        } catch { }   # index not published / network error: try the next one
    }
    return $null
}

$cuda = $null
$cc = $null
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $smi = (& nvidia-smi | Out-String)
    if ($smi -match 'CUDA Version:\s*([0-9]+\.[0-9]+)') { $cuda = $Matches[1] }
    $capRaw = (Invoke-NativeQuiet { & nvidia-smi --query-gpu=compute_cap --format=csv,noheader } | Select-Object -First 1)
    if ($capRaw -and ($capRaw.Trim() -match '^[0-9]+\.[0-9]+$')) { $cc = [double]$capRaw.Trim() }
}

# Install torch AND torchvision together from the same index. If only torch
# were installed, the requirements install would resolve torchvision from PyPI,
# and torchvision pins an exact torch version, so pip would uninstall the +cu
# build and replace it with the +cpu one, silently and with exit 0. Installing
# them together closes that hole; the final verification below checks it too.
$torchOk = $false
if ($cuda) {
    if ($cc -and $cc -lt 7.5) {
        $tag = 'cu126'
        Write-Host "NVIDIA GPU with compute capability $cc (pre-Turing): forcing $tag,"
        Write-Host "because cu128+ / CUDA 13 wheels no longer ship kernels for this card."
    } else {
        $tag = Select-TorchTag $cc $cuda
        if ($tag) {
            Write-Host "NVIDIA GPU detected (driver supports CUDA $cuda); using published index $tag"
        } else {
            Write-Warning "no published torch index <= CUDA $cuda; falling back to the default torch (likely CPU)"
        }
    }
    if ($tag) {
        $index = "https://download.pytorch.org/whl/$tag"
        & $venvPython -m pip install torch torchvision --index-url $index
        if ($LASTEXITCODE -eq 0) { $torchOk = $true }
        else { Write-Warning "the index $index failed; falling back to the default torch" }
    }
} else {
    Write-Host 'No NVIDIA GPU detected; installing the default torch (CPU)'
}

if (-not $torchOk) {
    Invoke-Checked 'pip install torch torchvision' { & $venvPython -m pip install torch torchvision }
}

Invoke-Checked 'pip install requirements' { & $venvPython -m pip install -r (Join-Path $ScriptDir 'requirements-ctrlregen.txt') }

# Final verification (AFTER requirements, the step that could have replaced the
# +cu torch with a +cpu one): if a GPU is present but the final torch has no
# CUDA support, warn loudly and exit non-zero instead of pretending success
# with a silent CPU-only environment.
if ($cc -or $cuda) {
    $probe = Invoke-NativeQuiet {
        & $venvPython -c "import torch; print(torch.cuda.is_available()); print(' '.join(torch.cuda.get_arch_list()))"
    }
    if ($LASTEXITCODE -eq 0 -and $probe) {
        $lines = @($probe)
        $cudaOk = ($lines[0].Trim() -eq 'True')
        $archs = $lines[1]
        if (-not $cudaOk) {
            Write-Warning 'NVIDIA GPU detected but torch.cuda.is_available() = False.'
            Write-Warning 'CtrlRegen would run on CPU (very slow) or fail on GPU.'
            Write-Warning 'Reinstall torch and torchvision together from a published index, e.g.:'
            Write-Warning "  & '$venvPython' -m pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu130"
            exit 1
        }
        if ($cc) {
            $smTarget = 'sm_' + ($cc -replace '\.', '')
            if ($archs -and ($archs -notmatch [regex]::Escape($smTarget))) {
                Write-Warning "the installed torch does NOT include $smTarget - CtrlRegen will fail on GPU."
                Write-Warning "available archs: $archs"
                Write-Warning "reinstall from an older index, e.g.:"
                Write-Warning "  & '$venvPython' -m pip install --force-reinstall torch torchvision --index-url https://download.pytorch.org/whl/cu126"
            } elseif ($archs) {
                Write-Host "OK: torch includes $smTarget (archs: $archs)"
            }
        }
    } else {
        Write-Warning 'could not verify torch after installing requirements'
    }
}

Write-Host ''
Write-Host 'Done. Remove a watermark with:'
Write-Host ''
Write-Host "  \`$env:NOAI_WATERMARK_DIR = '$Dir'"
Write-Host "  & '$venvPython' '$ScriptDir\clean_ctrlregen.py' IMAGE -o OUTPUT"