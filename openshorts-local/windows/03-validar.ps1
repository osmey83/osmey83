<#
.SYNOPSIS
  Validación de extremo a extremo. No declara nada funcional sin probarlo.
.DESCRIPTION
  Pruebas: instalación, GPU en el contenedor (torch, CTranslate2, ONNX),
  NVENC, Ollama CPU (y que NO use VRAM), conectividad contenedor->Ollama,
  MCP (initialize + tools/list + registro en Claude), pipeline completo con
  un video real (importar, transcribir, detectar, clip 9:16, subtítulos,
  metadatos con Ollama, exportación H.265) y manejo de errores.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\03-validar.ps1
  powershell -ExecutionPolicy Bypass -File .\03-validar.ps1 -Video D:\grabaciones\charla.mp4
#>
param([string]$Video, [switch]$SinPipeline)
$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\config.ps1"
$results = New-Object System.Collections.ArrayList
function Record($area, $name, [bool]$ok, $detail) {
    [void]$results.Add([ordered]@{ area = $area; prueba = $name; ok = $ok; detalle = "$detail".Trim() })
    if ($ok) { Write-Ok "$name · $detail" } else { Write-Bad "$name · $detail" }
}
function DockerPy($code) {
    $out = & docker exec openshorts-backend python -c $code 2>&1 | Out-String
    return @{ ok = ($LASTEXITCODE -eq 0); out = $out.Trim() }
}
$rv = Join-Path $STATE_DIR 'venv-rvshorts\Scripts\rvshorts.exe'
$api = "http://localhost:$PORT_BACKEND"
# El adaptador lee su configuración del entorno: la del kit (puertos y modelo
# elegidos por 01-instalar.ps1), solo para este proceso.
$env:OPENSHORTS_URL     = $api
$env:RVSHORTS_LLM_URL   = "http://127.0.0.1:$OLLAMA_CPU_PORT/v1"
$env:RVSHORTS_LLM_MODEL = $LLM_MODEL
$env:RVSHORTS_PROYECTOS = $PROYECTOS

Write-Step 'Instalación'
$head = (& git -C $OS_DIR rev-parse HEAD 2>&1 | Out-String).Trim()
Record 'instalacion' 'commit fijado' ($head -eq $OS_COMMIT) $head
$running = (& docker ps --filter 'name=openshorts-' --format '{{.Names}}:{{.Status}}' | Out-String).Trim()
Record 'instalacion' 'contenedores' ($running -match 'openshorts-backend') ($running -replace "`r?`n", ' | ')
try { $h = Invoke-RestMethod "$api/health" -TimeoutSec 5; Record 'instalacion' 'API /health' $true ($h | ConvertTo-Json -Compress) }
catch { Record 'instalacion' 'API /health' $false $_ }
Record 'instalacion' 'adaptador rvshorts' (Test-Path $rv) $rv

Write-Step 'GPU dentro del contenedor (RTX 5090 / Blackwell)'
# Con Docker Desktop + WSL 2, nvidia-smi suele estar en /usr/lib/wsl/lib, fuera del PATH.
$smi = ((Invoke-Native 'docker' @('exec', 'openshorts-backend', 'sh', '-c',
        'nvidia-smi -L 2>/dev/null || /usr/lib/wsl/lib/nvidia-smi -L')) | Out-String).Trim()
Record 'gpu' 'nvidia-smi en contenedor' ($LASTEXITCODE -eq 0) $smi
$t = DockerPy "import torch;ok=torch.cuda.is_available();print(ok, torch.__version__, torch.version.cuda, torch.cuda.get_device_capability(0) if ok else '-', torch.cuda.get_arch_list() if ok else '-')"
Record 'gpu' 'torch CUDA (sm_120)' ($t.ok -and $t.out -match '(?m)^True' -and $t.out -match 'sm_120|\(12, 0\)') $t.out
$c = DockerPy "import ctranslate2;print(ctranslate2.__version__, 'cuda_devices=', ctranslate2.get_cuda_device_count())"
Record 'gpu' 'CTranslate2 (faster-whisper) ve la GPU' ($c.ok -and $c.out -match 'cuda_devices= [1-9]') $c.out
$c2 = DockerPy "import numpy as np, ctranslate2; from faster_whisper import WhisperModel; m=WhisperModel('tiny', device='cuda', compute_type='float16'); s,i=m.transcribe(np.zeros(16000,dtype='float32')); list(s); print('whisper cuda ok', i.language)"
Record 'gpu' 'Whisper ejecuta en CUDA (kernel real en Blackwell)' $c2.ok ($c2.out -split "`n" | Select-Object -Last 2)
$o = DockerPy "import onnxruntime as o;print(o.get_available_providers())"
Record 'gpu' 'ONNX Runtime CUDA (Parakeet)' ($o.out -match 'CUDAExecutionProvider') $o.out

Write-Step 'FFmpeg / NVENC en el contenedor'
foreach ($enc in 'h264_nvenc', 'hevc_nvenc') {
    $null = & docker exec openshorts-backend ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc=size=256x256:rate=1 -frames:v 1 -c:v $enc -f null - 2>&1
    Record 'nvenc' "$enc (contenedor)" ($LASTEXITCODE -eq 0) 'codificación de 1 fotograma'
}
$log = (& docker logs openshorts-backend 2>&1 | Select-String 'Encoder\]' | Select-Object -Last 1 | Out-String).Trim()
if ($log) { Record 'nvenc' 'encoder elegido por OpenShorts' ($log -match 'nvenc') $log }
foreach ($enc in 'h264_nvenc', 'hevc_nvenc') {
    $null = & ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc=size=256x256:rate=1 -frames:v 1 -c:v $enc -f null - 2>&1
    Record 'nvenc' "$enc (ffmpeg del host, exportador)" ($LASTEXITCODE -eq 0) (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
}

Write-Step "Ollama solo-CPU (:$OLLAMA_CPU_PORT)"
try {
    $v = Invoke-RestMethod "http://127.0.0.1:$OLLAMA_CPU_PORT/api/version" -TimeoutSec 5
    Record 'ollama' 'API responde' $true "v$($v.version)"
    $body = @{ model = $LLM_MODEL; stream = $false; temperature = 0; response_format = @{ type = 'json_object' }
               messages = @(@{ role = 'user'; content = 'Devuelve solo {"ok": true, "idioma": "es"}' }) } | ConvertTo-Json -Depth 5
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $resp = Invoke-RestMethod "http://127.0.0.1:$OLLAMA_CPU_PORT/v1/chat/completions" -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 900
    $sw.Stop()
    $content = $resp.choices[0].message.content
    $parsed = $null; try { $parsed = $content | ConvertFrom-Json } catch {}
    Record 'ollama' "chat JSON con $LLM_MODEL" ([bool]$parsed) ("{0:N1}s · {1}" -f $sw.Elapsed.TotalSeconds, $content)
    $ps = Invoke-RestMethod "http://127.0.0.1:$OLLAMA_CPU_PORT/api/ps" -TimeoutSec 5
    $m = $ps.models | Where-Object { $_.name -like "$LLM_MODEL*" } | Select-Object -First 1
    if ($m) { Record 'ollama' 'se ejecuta en CPU (size_vram = 0)' ($m.size_vram -eq 0) ("RAM {0:N1} GB · VRAM {1:N1} GB" -f ($m.size / 1GB), ($m.size_vram / 1GB)) }
} catch { Record 'ollama' 'API / chat' $false $_ }
$conn = DockerPy "import urllib.request,json;print(json.load(urllib.request.urlopen('http://host.docker.internal:$OLLAMA_CPU_PORT/api/version',timeout=5)))"
Record 'ollama' 'contenedor -> host.docker.internal:Ollama' $conn.ok $conn.out
try {
    $cfg = Invoke-RestMethod "$api/api/config" -TimeoutSec 5
    Record 'ollama' 'OpenShorts usa el LLM local' ([bool]$cfg.localLlm) ($cfg.localLlm | ConvertTo-Json -Compress)
} catch { Record 'ollama' 'OpenShorts /api/config' $false $_ }

Write-Step 'MCP'
$hdr = @{ Accept = 'application/json, text/event-stream' }
try {
    $init = Invoke-RestMethod "$api/mcp" -Method Post -Headers $hdr -ContentType 'application/json' -Body (@{ jsonrpc = '2.0'; id = 1; method = 'initialize'; params = @{ protocolVersion = '2025-06-18'; capabilities = @{}; clientInfo = @{ name = 'validar'; version = '1' } } } | ConvertTo-Json -Depth 5)
    Record 'mcp' 'initialize' ($init.result.serverInfo.name -eq 'openshorts') ($init.result.serverInfo | ConvertTo-Json -Compress)
    $tl = Invoke-RestMethod "$api/mcp" -Method Post -Headers $hdr -ContentType 'application/json' -Body '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
    $names = @($tl.result.tools | ForEach-Object { $_.name })
    Record 'mcp' 'tools/list (8 herramientas)' ($names.Count -ge 8) ($names -join ', ')
    $bad = Invoke-RestMethod "$api/mcp" -Method Post -Headers $hdr -ContentType 'application/json' -Body '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_job_status","arguments":{"job_id":"no-existe"}}}'
    Record 'errores' 'MCP: job inexistente = error de herramienta, sin caída' ([bool]$bad.result.isError) (($bad.result.content | Select-Object -First 1).text)
} catch { Record 'mcp' 'endpoint /mcp' $false $_ }
if (Get-Command claude -ErrorAction SilentlyContinue) {
    $l = (& claude mcp list 2>&1 | Out-String)
    $line = ($l -split "`n" | Where-Object { $_ -match '^openshorts:' }) -join ''
    Record 'mcp' 'registrado en Claude Code' ($line -match 'Connected|✓|conectado') $line
}

Write-Step 'Manejo de errores'
try { $null = Invoke-RestMethod "$api/api/process" -Method Post -ContentType 'application/json' -Body '{"upload_id":"no-existe","acknowledged":true}'
      Record 'errores' 'upload_id inexistente' $false 'aceptado (no debería)' }
catch {
    $code = 0; if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
    Record 'errores' 'upload_id inexistente -> 404 con mensaje' ($code -eq 404) "HTTP $code $($_.ErrorDetails.Message)"
}
if (Test-Path $rv) {
    $null = Invoke-Native $rv @('nuevo', 'prueba-error', 'C:\no\existe.mp4')
    Record 'errores' 'rvshorts: video inexistente -> código 2' ($LASTEXITCODE -eq 2) "rc=$LASTEXITCODE"
    $diag = (Invoke-Native $rv @('--modelo', 'modelo-que-no-existe', 'diagnostico')) -join ' '
    Record 'errores' 'rvshorts: modelo ausente detectado' ($LASTEXITCODE -ne 0 -and $diag -match 'NO est') "rc=$LASTEXITCODE"
} else { Record 'errores' 'rvshorts instalado' $false "falta $rv" }

if (-not $SinPipeline) {
    Write-Step 'Pipeline completo (importar -> clip 9:16 -> metadatos -> H.265 -> revisión)'
    if (-not $Video) {
        # Video de demostración del propio repo (41 s) en bucle: OpenShorts exige >= 45 s.
        $Video = Join-Path $STATE_DIR 'demo-125s.mp4'
        if (-not (Test-Path $Video)) {
            & ffmpeg -hide_banner -loglevel error -y -stream_loop 2 -i (Join-Path $OS_DIR 'demo-openshorts.mp4') -c copy $Video
        }
    }
    $proj = 'validacion-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
    $sw = [Diagnostics.Stopwatch]::StartNew()
    & $rv pipeline $proj $Video --clips 2 --codec h265 --intervalo 10
    $rcPipe = $LASTEXITCODE
    $sw.Stop()
    $why = ''
    if ($rcPipe -eq 3) { $why = ' (ComfyUI tiene renders en cola: espera o usa --ignorar-cola)' }
    Record 'pipeline' 'rvshorts pipeline' ($rcPipe -eq 0) ("rc=$rcPipe en {0:N0}s$why" -f $sw.Elapsed.TotalSeconds)
    $pdir = Join-Path $PROYECTOS $proj
    $clips = @(Get-ChildItem (Join-Path $pdir 'clips') -Filter *.mp4 -ErrorAction SilentlyContinue)
    Record 'pipeline' 'clips generados' ($clips.Count -ge 1) "$($clips.Count) en $pdir\clips"
    foreach ($cf in $clips) {
        $pj = (& ffprobe -v error -select_streams v:0 -show_entries 'stream=width,height:format=duration' -of json $cf.FullName) -join "`n" | ConvertFrom-Json
        $w = [int]$pj.streams[0].width; $hgt = [int]$pj.streams[0].height
        $dur = [double]::Parse($pj.format.duration, [Globalization.CultureInfo]::InvariantCulture)
        $pr = "${w}x${hgt} $([math]::Round($dur,1))s"
        Record 'pipeline' "$($cf.Name) 9:16 y 15-60 s" ($hgt -gt 0 -and [math]::Abs($w / $hgt - 9 / 16) -lt 0.01 -and $dur -ge 14 -and $dur -le 61) "$pr"
        $tr = Get-Content (Join-Path $pdir ("meta\{0}.transcript.json" -f $cf.BaseName)) -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
        Record 'pipeline' "$($cf.Name) transcripción por palabra" ($tr.captions.Count -gt 5) "$($tr.captions.Count) palabras · $($tr.language)"
        $meta = Get-Content (Join-Path $pdir ("meta\{0}.json" -f $cf.BaseName)) -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
        Record 'pipeline' "$($cf.Name) metadatos Ollama" ([bool]$meta.hashtags.tiktok) ("gancho: " + $meta.hooks[0].text)
        $ex = Join-Path $pdir ("exports\{0}_h265.mp4" -f $cf.BaseName)
        $ec = (& ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 $ex 2>&1 | Out-String).Trim()
        Record 'pipeline' "$($cf.Name) exportación H.265" ($ec -eq 'hevc') $ex
    }
    $review = Get-Content (Join-Path $pdir 'review.json') -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
    $states = @($review.clips.PSObject.Properties | ForEach-Object { $_.Value.status })
    Record 'pipeline' 'todo queda pendiente de aprobación manual' (($states | Where-Object { $_ -ne 'pendiente' }).Count -eq 0 -and $states.Count -gt 0) ($states -join ',')
    Write-Host "  Revisa visualmente los subtítulos: $pdir\clips (no se puede comprobar por script)."
}

$out = Join-Path $STATE_DIR ("validacion-{0:yyyyMMdd-HHmmss}.json" -f (Get-Date))
$results | ConvertTo-Json -Depth 4 | Set-Content $out -Encoding UTF8
$fails = @($results | Where-Object { -not $_.ok })
Write-Step ("Resultado: {0}/{1} pruebas OK" -f ($results.Count - $fails.Count), $results.Count)
if ($fails.Count) { $fails | ForEach-Object { Write-Bad "$($_.area) · $($_.prueba): $($_.detalle)" } }
Write-Host "Informe: $out"
exit $fails.Count
