<#
.SYNOPSIS
  Port a Windows de setup_synthid.sh.

.DESCRIPTION
  Bootstrap del checkout externo reverse-SynthID para el scorer opcional de
  pixel. Es SOLO deteccion, no eliminacion.

  El proyecto upstream (https://github.com/aloshdenny/reverse-SynthID) esta
  bajo una Research License no comercial y NO se empaqueta en este
  repositorio. Este script lo clona en local e instala solo las dependencias
  que necesita score_synthid.py.

  Diferencia con la version .sh: el venv de Windows vive en .venv\Scripts\
  en vez de .venv/bin/.

.PARAMETER Dir
  Directorio del checkout (por defecto: $env:REVERSE_SYNTHID_DIR o ~\reverse-SynthID)

.PARAMETER Ref
  Commit a usar (por defecto: el SHA fijado; no apuntes a una rama movil)

.PARAMETER Full
  Instala el requirements.txt completo del upstream (anade torch/diffusers
  para el bypass del VAE) en vez de solo lo del scorer.

.PARAMETER Python
  Interprete usado para crear el venv (por defecto: python)
#>
[CmdletBinding()]
param(
    [string]$Dir,
    [string]$Ref = 'b11083676fd3ee3ff97ce9d03c0e409e46905902',
    [switch]$Full,
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Invoke-Checked {
    param([string]$What, [scriptblock]$Block)
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "$What fallo (codigo $LASTEXITCODE)" }
}

if (-not $Dir) {
    if ($env:REVERSE_SYNTHID_DIR) { $Dir = $env:REVERSE_SYNTHID_DIR }
    else { $Dir = Join-Path $HOME 'reverse-SynthID' }
}
$parent = Split-Path -Parent $Dir
if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Force -Path $Dir | Out-Null }
$Dir = (Resolve-Path $Dir).Path

if (-not (Test-Path (Join-Path $Dir '.git'))) {
    Write-Host "Clonando reverse-SynthID en $Dir (ref fijado: $Ref)"
    Invoke-Checked 'git clone' { git clone --depth 1 --filter=blob:none --sparse https://github.com/aloshdenny/reverse-SynthID.git $Dir }
    Invoke-Checked 'git fetch' { git -C $Dir fetch --depth 1 origin $Ref }
    Invoke-Checked 'git checkout' { git -C $Dir checkout --detach $Ref }
    Invoke-Checked 'sparse-checkout' {
        git -C $Dir sparse-checkout set --no-cone '/src/' '/artifacts/spectral_codebook_v4.npz' '/requirements.txt' '/LICENSE' '/README.md'
    }
    $head = (git -C $Dir rev-parse HEAD).Trim()
    if ($head -ne $Ref) { throw "error: se esperaba el ref fijado $Ref, se obtuvo $head" }
} else {
    Write-Host "Usando checkout existente: $Dir"
    $head = ''
    try { $head = "$(git -C $Dir rev-parse HEAD)".Trim() } catch { $head = '' }
    if ($head -ne $Ref) {
        Write-Host "checkout existente no esta en el ref fijado $Ref (HEAD: $head); re-fijando"
        Invoke-Checked 'git fetch' { git -C $Dir fetch --depth 1 origin $Ref }
        Invoke-Checked 'git checkout' { git -C $Dir checkout --detach $Ref }
        Invoke-Checked 'sparse-checkout' {
            git -C $Dir sparse-checkout set --no-cone '/src/' '/artifacts/spectral_codebook_v4.npz' '/requirements.txt' '/LICENSE' '/README.md'
        }
        $head = (git -C $Dir rev-parse HEAD).Trim()
        if ($head -ne $Ref) { throw "error: se esperaba el ref fijado $Ref, se obtuvo $head" }
    }
}

$venvPython = Join-Path $Dir '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host "Creando venv en $Dir\.venv"
    Invoke-Checked 'crear venv' { & $Python -m venv (Join-Path $Dir '.venv') }
}

Write-Host 'Instalando dependencias de Python'
# pip fijado (un --upgrade pip sin pin es un punto de deriva de cadena de suministro).
Invoke-Checked 'pip install pip' { & $venvPython -m pip install --upgrade 'pip==26.2.1' }

if ($Full) {
    Write-Host 'Instalando el requirements.txt completo del upstream (incluye torch/diffusers)'
    Invoke-Checked 'pip install upstream' { & $venvPython -m pip install -r (Join-Path $Dir 'requirements.txt') }
} else {
    Write-Host 'Instalando solo las dependencias del scorer'
    Invoke-Checked 'pip install scorer' { & $venvPython -m pip install -r (Join-Path $ScriptDir 'requirements-synthid-scorer.txt') }
}

$codebook = Join-Path $Dir 'artifacts\spectral_codebook_v4.npz'
if (-not (Test-Path $codebook)) {
    Write-Warning "codebook no encontrado en $codebook"
    Write-Warning "ejecuta: git -C '$Dir' sparse-checkout add '/artifacts/spectral_codebook_v4.npz'"
}

Write-Host ''
Write-Host 'Listo. Para puntuar una imagen:'
Write-Host ''
Write-Host "  `$env:REVERSE_SYNTHID_DIR = '$Dir'"
Write-Host "  python '$ScriptDir\inspect_image.py' IMAGEN"
