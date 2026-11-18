<#
.SYNOPSIS
  Auditoría SOLO LECTURA del equipo antes de instalar OpenShorts.
  No instala, no modifica, no descarga nada (salvo -ProbarGpuDocker, que
  descarga la imagen oficial nvidia/cuda para probar la GPU en Docker).
.PARAMETER ConsultarRenderVibe
  Además llama a busy_reason() de RenderVibe con su .venv (python -B). Eso
  IMPORTA código de RenderVibe; por eso no se hace sin este interruptor.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\00-auditar.ps1
  powershell -ExecutionPolicy Bypass -File .\00-auditar.ps1 -ProbarGpuDocker
#>
param(
    [string]$RenderVibe = 'C:\Users\ROG\Documents\Codex\2026-11-06\referenced-chatgpt-conversation-this-is-an-2\Miniserie-Studio',
    [switch]$ProbarGpuDocker,
    [switch]$ConsultarRenderVibe
)
$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\config.ps1"
$r = [ordered]@{ fecha = (Get-Date).ToString('s'); equipo = $env:COMPUTERNAME }

Write-Step 'Sistema'
$osInfo = Get-CimInstance Win32_OperatingSystem
$r.windows = "$($osInfo.Caption) build $($osInfo.BuildNumber) $($osInfo.OSArchitecture)"
$r.ram_gb  = [math]::Round($osInfo.TotalVisibleMemorySize / 1MB, 1)
$r.cpu     = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name
$r.cpu_hilos = (Get-CimInstance Win32_Processor | Measure-Object NumberOfLogicalProcessors -Sum).Sum
$r.disco_libre_gb = [ordered]@{}
Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Free } | ForEach-Object {
    $r.disco_libre_gb[$_.Name] = [math]::Round($_.Free / 1GB, 1) }
Write-Ok "$($r.windows) · $($r.ram_gb) GB RAM · $($r.cpu) ($($r.cpu_hilos) hilos)"

Write-Step 'GPU NVIDIA (Blackwell = compute capability 12.0)'
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    $q = & nvidia-smi '--query-gpu=name,driver_version,memory.total,compute_cap' '--format=csv,noheader' 2>&1
    $r.gpu = "$q".Trim()
    Write-Ok $r.gpu
    $drv = ("$q" -split ',')[1].Trim()
    $major = [int](($drv -split '\.')[0])
    $r.driver_major = $major
    if ($major -ge 580) { Write-Ok "Driver $drv >= R580: compatible con la imagen GPU de OpenShorts (torch 2.11 / CUDA 13)" }
    elseif ($major -ge 570) { Write-Warn2 "Driver ${drv}: soporta la 5090 y CUDA 12.8, pero la imagen GPU de OpenShorts usa CUDA 13 (R580+). NVENC y Whisper (CUDA 12) deberían ir; torch en GPU no. No se actualiza nada automáticamente." }
    else { Write-Bad "Driver $drv < 570: la RTX 5090 necesita R570+." }
    $uso = & nvidia-smi '--query-compute-apps=pid,process_name,used_memory' '--format=csv,noheader' 2>&1
    $r.gpu_procesos = "$uso".Trim()
    if ($r.gpu_procesos) { Write-Host "  Procesos usando la GPU ahora: $($r.gpu_procesos)" }
} else { Write-Bad 'nvidia-smi no encontrado'; $r.gpu = $null }
$r.nvcc = Get-CmdVersion 'nvcc' @('--version')

Write-Step 'Herramientas'
$r.node    = Get-CmdVersion 'node' @('--version')
$r.npm     = Get-CmdVersion 'npm' @('--version')
$r.git     = Get-CmdVersion 'git' @('--version')
$r.python  = Get-CmdVersion 'python' @('--version')
$r.py_launcher = $null
if (Get-Command py -ErrorAction SilentlyContinue) { $r.py_launcher = (& py -0p 2>&1 | Out-String).Trim() }
$r.ffmpeg  = Get-CmdVersion 'ffmpeg' @('-hide_banner', '-version')
$r.ffmpeg_path = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
$r.claude  = Get-CmdVersion 'claude' @('--version')
$r.docker  = Get-CmdVersion 'docker' @('--version')
$r.compose = Get-CmdVersion 'docker' @('compose', 'version')
foreach ($k in 'node','npm','git','python','ffmpeg','claude','docker','compose') {
    if ($r[$k]) { Write-Ok "$k : $($r[$k])" } else { Write-Warn2 "$k : no encontrado" } }
if ($r.py_launcher) { Write-Host "  Pythons instalados (py -0p):`n$($r.py_launcher)" }

Write-Step 'FFmpeg y NVENC (prueba real de 1 fotograma)'
$r.nvenc = [ordered]@{}
if ($r.ffmpeg_path) {
    foreach ($enc in 'h264_nvenc','hevc_nvenc','av1_nvenc','libx264','libx265') {
        $null = & ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc=size=256x256:rate=1 -frames:v 1 -c:v $enc -f null - 2>&1
        $ok = ($LASTEXITCODE -eq 0); $r.nvenc[$enc] = $ok
        if ($ok) { Write-Ok $enc } else { Write-Warn2 "$enc no funciona con este ffmpeg" }
    }
}

Write-Step 'Ollama'
$r.ollama = Get-CmdVersion 'ollama' @('--version')
if ($r.ollama) {
    Write-Ok $r.ollama
    $r.ollama_modelos = (& ollama list 2>&1 | Out-String).Trim()
    Write-Host $r.ollama_modelos
    $r.ollama_models_dir = [Environment]::GetEnvironmentVariable('OLLAMA_MODELS', 'User')
    $r.ollama_host_var   = [Environment]::GetEnvironmentVariable('OLLAMA_HOST', 'User')
    try {
        $v = Invoke-RestMethod "http://127.0.0.1:$OLLAMA_MAIN_PORT/api/version" -TimeoutSec 5
        $r.ollama_api = $v.version; Write-Ok "API en :$OLLAMA_MAIN_PORT responde (v$($v.version))"
        $ps = Invoke-RestMethod "http://127.0.0.1:$OLLAMA_MAIN_PORT/api/ps" -TimeoutSec 5
        $r.ollama_cargados = @($ps.models | ForEach-Object { "$($_.name) vram=$([math]::Round($_.size_vram/1GB,1))GB" })
    } catch { Write-Warn2 "La API de Ollama no responde en :$OLLAMA_MAIN_PORT ($_)" }
} else { Write-Bad 'ollama no encontrado' }

Write-Step 'Docker / WSL2'
$r.wsl = (& wsl --status 2>&1 | Out-String).Trim() -replace "`0", ''
try { $r.docker_server = (& docker info --format '{{.ServerVersion}} {{.OperatingSystem}}' 2>&1 | Out-String).Trim() } catch {}
if ($r.docker_server -and $r.docker_server -notmatch 'error') { Write-Ok "Docker Engine: $($r.docker_server)" }
else { Write-Warn2 "Docker no está arrancado o no está instalado: $($r.docker_server)" }
if ($ProbarGpuDocker -and $r.docker) {
    Write-Host '  Probando GPU dentro de Docker (descarga nvidia/cuda:12.8.1-base-ubuntu24.04 si falta)...'
    $g = & docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi -L 2>&1 | Out-String
    $r.docker_gpu = $g.Trim()
    if ($LASTEXITCODE -eq 0) { Write-Ok "GPU visible en contenedores: $($r.docker_gpu)" } else { Write-Bad $r.docker_gpu }
}

Write-Step 'Puertos y servicios existentes'
$r.puertos = [ordered]@{}
foreach ($p in @($PORT_BACKEND, $PORT_FRONTEND, $PORT_RENDERER) + $RESERVED_PORTS) {
    $own = Get-PortOwner $p; $r.puertos["$p"] = $own
    if ($own) { Write-Host "  :$p ocupado por $own" } else { Write-Host "  :$p libre" }
}
$comfy = Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -match 'ComfyUI|comfy' } | Select-Object -First 3
$r.comfyui_procesos = @($comfy | ForEach-Object { "PID $($_.ProcessId): $($_.CommandLine)" })
if ($r.comfyui_procesos) { Write-Ok "ComfyUI en ejecución: $($r.comfyui_procesos -join ' | ')" }
else { Write-Host '  ComfyUI no está en ejecución ahora (no es necesario).' }
$r.comfyui_api = [ordered]@{}
foreach ($p in $PORT_COMFYUI, $PORT_COMFYUI_ALT, 8188) {
    try {
        $s = Invoke-RestMethod "http://127.0.0.1:$p/system_stats" -TimeoutSec 3
        $q = Invoke-RestMethod "http://127.0.0.1:$p/queue" -TimeoutSec 3
        $r.comfyui_api["$p"] = "ComfyUI $($s.system.comfyui_version) · cola: $(@($q.queue_running).Count) en curso, $(@($q.queue_pending).Count) pendientes"
        Write-Ok ":$p $($r.comfyui_api["$p"])"
    } catch { $r.comfyui_api["$p"] = $null }
}

Write-Step "RenderVibe Lab (solo lectura): $RenderVibe"
$rv = [ordered]@{ ruta = $RenderVibe; existe = (Test-Path $RenderVibe) }
if ($rv.existe) {
    $skip = '\\(node_modules|\.git|\.venv|venv|__pycache__|dist|build|\.next|output|models)(\\|$)'
    $files = Get-ChildItem $RenderVibe -Recurse -File -Depth 4 -ErrorAction SilentlyContinue |
             Where-Object { $_.FullName -notmatch $skip }
    $rv.archivos = $files.Count
    $rv.arbol = @(Get-ChildItem $RenderVibe -Recurse -Directory -Depth 2 -ErrorAction SilentlyContinue |
                  Where-Object { $_.FullName -notmatch $skip } |
                  ForEach-Object { $_.FullName.Substring($RenderVibe.Length) })
    $manifests = 'package.json','pyproject.toml','requirements*.txt','docker-compose*.yml',
                 'compose*.yaml','Dockerfile','*.ps1','*.bat','.env.example','README*','CLAUDE.md',
                 'AGENTS.md','.mcp.json','settings.json','vite.config.*','next.config.*'
    $rv.manifiestos = @($files | Where-Object { $n = $_.Name; ($manifests | Where-Object { $n -like $_ }) } |
                        ForEach-Object { $_.FullName.Substring($RenderVibe.Length) })
    $rv.extensiones = $files | Group-Object Extension | Sort-Object Count -Descending |
                      Select-Object -First 12 | ForEach-Object { "$($_.Name)=$($_.Count)" }
    # Puertos citados en configuración (para no chocar). No se leen secretos.
    $cfg = $files | Where-Object { $_.Extension -in '.json','.yml','.yaml','.toml','.py','.ts','.js','.ps1','.bat','.example' -and $_.Length -lt 512KB }
    $ports = @{}
    foreach ($f in $cfg) {
        $m = Select-String -Path $f.FullName -Pattern '(?:localhost|127\.0\.0\.1|0\.0\.0\.0|PORT\D{0,4})[:=\s]*(\d{4,5})' -AllMatches -ErrorAction SilentlyContinue
        foreach ($hit in $m) { foreach ($mm in $hit.Matches) { $ports[$mm.Groups[1].Value] = $f.Name } }
    }
    $rv.puertos_citados = $ports
    # Solo los NOMBRES de variables de .env, nunca sus valores.
    $rv.env_vars = @(Get-ChildItem $RenderVibe -Filter '.env*' -Recurse -Depth 2 -File -ErrorAction SilentlyContinue |
        ForEach-Object { Get-Content $_.FullName -ErrorAction SilentlyContinue } |
        Where-Object { $_ -match '^\s*([A-Z0-9_]+)\s*=' } | ForEach-Object { $Matches[1] } | Sort-Object -Unique)
    $rv.venvs = @(Get-ChildItem $RenderVibe -Recurse -Depth 3 -Filter 'pyvenv.cfg' -File -ErrorAction SilentlyContinue |
                  ForEach-Object { $_.DirectoryName.Substring($RenderVibe.Length) })
    # Estado del estudio con SU propio .venv; -B para no escribir .pyc en el proyecto.
    # Solo con -ConsultarRenderVibe: importa backend.launcher de RenderVibe
    # (ejecuta su código al importarlo), así que no va por defecto.
    $rvPy = Join-Path $RenderVibe '.venv\Scripts\python.exe'
    if ($ConsultarRenderVibe -and (Test-Path $rvPy)) {
        Push-Location $RenderVibe
        try { $rv.busy_reason = (& $rvPy -B -c "from backend.launcher import busy_reason; print(busy_reason(check_comfy=False))" 2>&1 | Out-String).Trim() }
        finally { Pop-Location }
        Write-Host "  busy_reason(): $($rv.busy_reason)  (None = sin trabajos activos)"
    }
    $rv.coordinacion = Test-Path (Join-Path $RenderVibe 'COORDINACION.md')
    Write-Ok "$($rv.archivos) archivos · manifiestos: $($rv.manifiestos -join ', ')"
    Write-Host "  Puertos citados: $(($ports.Keys | Sort-Object) -join ', ')"
} else { Write-Warn2 'La ruta no existe en este equipo.' }
$r.rendervibe = $rv

$out = Join-Path $STATE_DIR ("auditoria-{0:yyyyMMdd-HHmmss}.json" -f (Get-Date))
$r | ConvertTo-Json -Depth 6 | Set-Content -Path $out -Encoding UTF8
Write-Step 'Resultado'
Write-Host "Informe guardado en: $out"
Write-Host 'Pégale este JSON a Claude para ajustar la instalación a tu equipo.'
