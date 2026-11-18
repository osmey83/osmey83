# Configuración compartida del kit (se carga con: . "$PSScriptRoot\config.ps1").
# Para cambiar algo sin editar este archivo, crea config.local.ps1 al lado:
# lo genera 01-instalar.ps1 y tiene prioridad.

$OS_REPO      = 'https://github.com/mutonby/openshorts.git'
$OS_COMMIT    = 'c4b496a73747664d9d0864b19c34085303e90d14'   # auditado el 2026-11-17
# Todo en D: dentro de Comfy Desktop, en su propia subcarpeta (no toca ComfyUI).
# La carpeta final se llama 'openshorts' porque Docker Compose usa ese nombre de proyecto.
$OS_DIR       = 'D:\Comfy Desktop\OpenShorts\openshorts'
$KIT_DIR      = Split-Path -Parent $PSScriptRoot             # ...\openshorts-local
$STATE_DIR    = Join-Path $env:LOCALAPPDATA 'openshorts-local'
$PROYECTOS    = 'D:\Comfy Desktop\OpenShorts\proyectos'

# Puertos del host. 8000/5175/3100 son los de OpenShorts; si están ocupados,
# 01-instalar.ps1 elige otros y los guarda en config.local.ps1.
$PORT_BACKEND  = 8000
$PORT_FRONTEND = 5175
$PORT_RENDERER = 3100

# Ollama: tu instancia actual (11434) NO se toca. Se arranca una segunda
# instancia solo-CPU en 11435 que lee los mismos modelos (sin descargas dobles).
$OLLAMA_MAIN_PORT = 11434
$OLLAMA_CPU_PORT  = 11435
$OLLAMA_CTX       = 16384                 # OpenShorts necesita > 4096 (README)
# Ya descargado en este PC (auditoría 2026-11-17): sin descargas nuevas.
# Alternativas instaladas: llama3.1:8b (por defecto de OpenShorts),
# phi4:14b, mistral-small3.2:24b (mejor español, más lento en CPU).
$LLM_MODEL        = 'qwen2.5:7b-instruct'

# Puertos de servicios existentes que hay que respetar. Auditoría 2026-11-17:
# Comfy Desktop corre ComfyUI en 8195 (--port 8195); RenderVibe Studio en 8295;
# la config de RenderVibe cita además 8189-8191 y 8297. 8188 = ComfyUI por defecto.
$PORT_COMFYUI     = 8195
$PORT_COMFYUI_ALT = 8189
$PORT_RENDERVIBE  = 8295
$RESERVED_PORTS   = @(8188, 8189, 8190, 8191, 8195, 8295, 8297, 11434, 11435)

$local = Join-Path $PSScriptRoot 'config.local.ps1'
if (Test-Path $local) { . $local }

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  [OK]    $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "  [AVISO] $msg" -ForegroundColor Yellow }
function Write-Bad($msg)  { Write-Host "  [FALLO] $msg" -ForegroundColor Red }

function Test-PortInUse([int]$port) {
    $c = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    return [bool]$c
}

function Get-PortOwner([int]$port) {
    $c = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
         Select-Object -First 1
    if (-not $c) { return $null }
    $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
    if ($p) { return "$($p.ProcessName) (PID $($p.Id))" }
    return "PID $($c.OwningProcess)"
}

# PS 5.1: con $ErrorActionPreference='Stop', una línea en stderr de un
# ejecutable nativo (git, docker, py, ollama) se convierte en excepción.
# Invoke-Native ejecuta con 'Continue' y devuelve la salida; el éxito se
# juzga con $LASTEXITCODE.
function Invoke-Native([string]$exe, [string[]]$argList) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { return (& $exe @argList 2>&1 | ForEach-Object { "$_" }) }
    finally { $ErrorActionPreference = $prev }
}

function Get-CmdVersion([string]$exe, [string[]]$argList) {
    $cmd = Get-Command $exe -ErrorAction SilentlyContinue
    if (-not $cmd) { return $null }
    try {
        $out = & $cmd.Source @argList 2>&1 | Select-Object -First 1
        return ("$out").Trim()
    } catch { return "instalado (sin versión: $_)" }
}

if (-not (Test-Path $STATE_DIR)) { New-Item -ItemType Directory -Path $STATE_DIR | Out-Null }
