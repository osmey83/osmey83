<#
.SYNOPSIS
  Instala OpenShorts en una carpeta aislada (Docker + GPU) y el adaptador
  rvshorts en su propio venv. No toca Ollama, ComfyUI, CUDA, PyTorch, drivers
  ni ningún paquete global.
.DESCRIPTION
  Fases:
   1. Requisitos (git, Docker con WSL2, Python para el adaptador)
   2. Clonar mutonby/openshorts en $OS_DIR y fijar el commit auditado
   3. Elegir puertos libres (sin pisar ComfyUI ni procesos existentes)
   4. Escribir .env y docker-compose.override.yml (GPU + NVENC + Ollama CPU)
   5. Venv aislado para rvshorts
   6. docker compose build (imagen GPU, ~10-15 GB, tarda)
  Si .env u override ya existen, NO se sobrescriben salvo con -Forzar, y
  entonces se guarda antes una copia .bak con fecha.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\01-instalar.ps1
#>
param(
    [switch]$Forzar,
    [switch]$SinConstruir,
    [switch]$SinGpu,
    [string]$Modelo
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\config.ps1"
if ($Modelo) { $LLM_MODEL = $Modelo }

function Backup-IfExists([string]$path) {
    if (Test-Path $path) {
        $bak = "$path.bak-{0:yyyyMMdd-HHmmss}" -f (Get-Date)
        Copy-Item $path $bak
        Write-Warn2 "Copia de seguridad: $bak"
    }
}

function Find-FreePort([int]$preferred, [int]$alternative) {
    if (-not (Test-PortInUse $preferred)) { return $preferred }
    for ($p = $alternative; $p -lt $alternative + 50; $p++) {
        if (-not (Test-PortInUse $p) -and $RESERVED_PORTS -notcontains $p) { return $p }
    }
    throw "No hay puerto libre cerca de $alternative"
}

# --- 1. Requisitos --------------------------------------------------------
Write-Step '1/6 Requisitos'
foreach ($exe in 'git', 'docker') {
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
        throw "Falta $exe. Instálalo (Git for Windows / Docker Desktop con backend WSL2) y repite."
    }
}
$null = Invoke-Native 'docker' @('info')
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop no está arrancado.' }
$composeVer = ((Invoke-Native 'docker' @('compose', 'version', '--short')) | Out-String).Trim()
Write-Ok "docker compose $composeVer"
$py = $null
foreach ($cand in @('py -3.12', 'py -3.11', 'py -3', 'python')) {
    $parts = $cand -split ' '
    if (Get-Command $parts[0] -ErrorAction SilentlyContinue) {
        $pyArgs = @($parts[1..9] | Where-Object { $_ }) + @('-c', 'import sys;print(sys.version_info>=(3,10))')
        $v = Invoke-Native $parts[0] $pyArgs
        if ($LASTEXITCODE -eq 0 -and ("$v".Trim() -match '(?m)^True$')) { $py = $parts; break }
    }
}
if (-not $py) { throw 'Hace falta Python 3.10+ para el adaptador (no se instala nada global).' }
Write-Ok "Python para el adaptador: $($py -join ' ')"

# --- 2. Código fuente fijado ---------------------------------------------
Write-Step "2/6 OpenShorts en $OS_DIR (commit $($OS_COMMIT.Substring(0,7)))"
if (Test-Path (Join-Path $OS_DIR '.git')) {
    Push-Location $OS_DIR
    $null = Invoke-Native 'git' @('config', 'core.autocrlf', 'false')
    $dirty = ((Invoke-Native 'git' @('status', '--porcelain', '--untracked-files=no')) | Out-String).Trim()
    if ($dirty) { Pop-Location; throw "Hay cambios locales en $OS_DIR; no se toca. Revísalos con git status." }
    $null = Invoke-Native 'git' @('fetch', '--depth', '1', 'origin', $OS_COMMIT)
    $null = Invoke-Native 'git' @('-c', 'advice.detachedHead=false', 'checkout', '--quiet', $OS_COMMIT)
    $rcGit = $LASTEXITCODE
    Pop-Location
    if ($rcGit -ne 0) { throw "git checkout $OS_COMMIT falló" }
} elseif (Test-Path $OS_DIR) {
    throw "$OS_DIR existe y no es un repositorio git; elige otra carpeta en config.local.ps1"
} else {
    New-Item -ItemType Directory -Path $OS_DIR -Force | Out-Null
    # --config (no -c) deja core.autocrlf=false GUARDADO en el repo: los
    # scripts sh del contenedor (bin/ffmpeg) necesitan finales LF.
    $null = Invoke-Native 'git' @('clone', '--config', 'core.autocrlf=false', '--filter=blob:none', $OS_REPO, $OS_DIR)
    if ($LASTEXITCODE -ne 0) { throw 'git clone falló' }
    $null = Invoke-Native 'git' @('-C', $OS_DIR, '-c', 'advice.detachedHead=false', 'checkout', '--quiet', $OS_COMMIT)
    if ($LASTEXITCODE -ne 0) { throw "git checkout $OS_COMMIT falló" }
}
$head = ((Invoke-Native 'git' @('-C', $OS_DIR, 'rev-parse', 'HEAD')) | Out-String).Trim()
if ($head -ne $OS_COMMIT) { throw "HEAD $head no coincide con el commit auditado" }
Write-Ok "Código en $head (licencia MIT)"

# --- 3. Puertos ------------------------------------------------------------
Write-Step '3/6 Puertos'
$owners = @{}
foreach ($p in $PORT_BACKEND, $PORT_FRONTEND, $PORT_RENDERER) { $owners[$p] = Get-PortOwner $p }
# Si los ocupa el propio OpenShorts (reinstalación), se mantienen.
$ours = ((Invoke-Native 'docker' @('ps', '--filter', 'name=openshorts-', '--format', '{{.Names}}')) | Out-String).Trim()
if ($ours) {
    Write-Ok "OpenShorts ya está corriendo ($ours); se conservan sus puertos."
    $pb, $pf, $pr = $PORT_BACKEND, $PORT_FRONTEND, $PORT_RENDERER
} else {
    $pb = Find-FreePort $PORT_BACKEND 18000
    $pf = Find-FreePort $PORT_FRONTEND 15175
    $pr = Find-FreePort $PORT_RENDERER 13100
}
foreach ($pair in @(@($PORT_BACKEND, $pb), @($PORT_FRONTEND, $pf), @($PORT_RENDERER, $pr))) {
    if ($pair[0] -ne $pair[1]) { Write-Warn2 ":$($pair[0]) ocupado por $($owners[$pair[0]]) -> se usa :$($pair[1])" }
    else { Write-Ok ":$($pair[0]) libre" }
}
$remap = ($pb -ne 8000) -or ($pf -ne 5175) -or ($pr -ne 3100)
if ($remap) {
    $cv = [version](($composeVer -replace '[^\d\.].*$', ''))
    if ($cv -lt [version]'2.24.4') { throw "Para cambiar puertos hace falta docker compose >= 2.24.4 (tienes $composeVer)" }
}
@"
# Generado por 01-instalar.ps1 el $(Get-Date -Format s)
`$PORT_BACKEND  = $pb
`$PORT_FRONTEND = $pf
`$PORT_RENDERER = $pr
`$LLM_MODEL     = '$LLM_MODEL'
"@ | Set-Content (Join-Path $PSScriptRoot 'config.local.ps1') -Encoding UTF8

# --- 4. .env y override ---------------------------------------------------
Write-Step '4/6 Configuración (.env + docker-compose.override.yml)'
$envPath = Join-Path $OS_DIR '.env'
$ovPath  = Join-Path $OS_DIR 'docker-compose.override.yml'
$whisperDevice = 'cuda'; $whisperCompute = 'float16'; $whisperModel = 'large-v3-turbo'
if ($SinGpu) { $whisperDevice = 'cpu'; $whisperCompute = 'int8'; $whisperModel = 'small' }

$envBody = Get-Content (Join-Path $PSScriptRoot 'plantillas\openshorts.env') -Raw
$envBody = $envBody.Replace('{{OLLAMA_CPU_PORT}}', "$OLLAMA_CPU_PORT")
$envBody = $envBody.Replace('{{LLM_MODEL}}', $LLM_MODEL)
$envBody = $envBody.Replace('{{WHISPER_MODEL}}', $whisperModel)
$envBody = $envBody.Replace('{{WHISPER_DEVICE}}', $whisperDevice)
$envBody = $envBody.Replace('{{WHISPER_COMPUTE}}', $whisperCompute)
if ((Test-Path $envPath) -and -not $Forzar) {
    Write-Warn2 ".env ya existe: no se modifica. Compáralo con plantillas\openshorts.env o usa -Forzar."
} else {
    Backup-IfExists $envPath
    [IO.File]::WriteAllText($envPath, $envBody, (New-Object Text.UTF8Encoding $false))
    Write-Ok ".env escrito (LLM: Ollama CPU :$OLLAMA_CPU_PORT · $LLM_MODEL · Whisper $whisperModel/$whisperDevice)"
}

$ports = ''
if ($remap) {
    $ports = @"
    ports: !override
      - "${pb}:8000"
"@
}
$gpuBlock = @"
    build:
      context: .
      args:
        GPU: "1"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu, video]
"@
if ($SinGpu) { $gpuBlock = '' }
$backendBody = ($gpuBlock + $ports).Trim()
if (-not $backendBody) { $gpuBlock = "    environment: {}`n" }   # backend: no puede quedar vacío
$ov = @"
# Generado por openshorts-local/windows/01-instalar.ps1 — ver README del kit.
services:
  backend:
$gpuBlock
$ports
"@
if ($remap) {
    $ov += @"

  frontend:
    ports: !override
      - "${pf}:5173"
  renderer:
    ports: !override
      - "${pr}:3100"
"@
}
if ((Test-Path $ovPath) -and -not $Forzar) {
    Write-Warn2 'docker-compose.override.yml ya existe: no se modifica (usa -Forzar).'
} else {
    Backup-IfExists $ovPath
    [IO.File]::WriteAllText($ovPath, $ov, (New-Object Text.UTF8Encoding $false))
    Write-Ok 'docker-compose.override.yml escrito'
}
Push-Location $OS_DIR
$cfgOut = Invoke-Native 'docker' @('compose', 'config', '--quiet')
$okCfg = ($LASTEXITCODE -eq 0)
if (-not $okCfg) { Write-Host ($cfgOut -join "`n") }
Pop-Location
if (-not $okCfg) { throw 'docker compose config no valida el override; revisa docker-compose.override.yml' }
Write-Ok 'docker compose config válido'

# --- 5. Adaptador --------------------------------------------------------
Write-Step '5/6 Adaptador rvshorts (venv propio)'
$venv = Join-Path $STATE_DIR 'venv-rvshorts'
if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
    $null = Invoke-Native $py[0] (@($py[1..9] | Where-Object { $_ }) + @('-m', 'venv', $venv))
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el venv del adaptador' }
}
$pipOut = Invoke-Native (Join-Path $venv 'Scripts\python.exe') @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '-e', (Join-Path $KIT_DIR 'adapter'))
if ($LASTEXITCODE -ne 0) { Write-Host ($pipOut -join "`n") }
if ($LASTEXITCODE -ne 0) { throw 'pip install del adaptador falló' }
New-Item -ItemType Directory -Path $PROYECTOS -Force | Out-Null
# Lanzador con la configuración de ESTE equipo (puerto, modelo, carpeta), sin
# variables de entorno globales: rvshorts.cmd <comando> ...
$launcher = Join-Path $STATE_DIR 'rvshorts.cmd'
$cmdBody = "@echo off`r`n" +
    "set OPENSHORTS_URL=http://localhost:$pb`r`n" +
    "set RVSHORTS_LLM_URL=http://127.0.0.1:$OLLAMA_CPU_PORT/v1`r`n" +
    "set RVSHORTS_LLM_MODEL=$LLM_MODEL`r`n" +
    "set RVSHORTS_PROYECTOS=$PROYECTOS`r`n" +
    "`"%~dp0venv-rvshorts\Scripts\rvshorts.exe`" %*`r`n"
[IO.File]::WriteAllText($launcher, $cmdBody, (New-Object Text.ASCIIEncoding))
Write-Ok "rvshorts: $launcher · proyectos en $PROYECTOS"

# --- 6. Imagen ----------------------------------------------------------------
if ($SinConstruir) { Write-Warn2 'Construcción omitida (-SinConstruir).'; return }
Write-Step '6/6 docker compose build (la imagen GPU tarda y ocupa ~10-15 GB)'
Push-Location $OS_DIR
$prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
& docker compose build
$rc = $LASTEXITCODE
$ErrorActionPreference = $prevEap
Pop-Location
if ($rc -ne 0) { throw "docker compose build falló (código $rc)" }
Write-Ok 'Imagen construida. Siguiente: .\iniciar.ps1 y luego .\03-validar.ps1'
