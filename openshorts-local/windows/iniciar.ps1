<#
.SYNOPSIS
  Arranca la instancia Ollama solo-CPU (:11435) y los contenedores de OpenShorts.
  Tu Ollama principal (:11434) y ComfyUI no se tocan.
#>
param([switch]$SinOllama)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\config.ps1"
$pidFile = Join-Path $STATE_DIR 'ollama-cpu.pid'

if (-not $SinOllama) {
    Write-Step "Ollama solo-CPU en 127.0.0.1:$OLLAMA_CPU_PORT"
    if (Test-PortInUse $OLLAMA_CPU_PORT) {
        Write-Ok "Ya hay algo escuchando en :$OLLAMA_CPU_PORT ($(Get-PortOwner $OLLAMA_CPU_PORT)); se reutiliza."
    } else {
        $ollama = (Get-Command ollama -ErrorAction Stop).Source
        # Variables SOLO para este proceso hijo; no se cambia nada global.
        $saved = @{}
        $vars = @{
            OLLAMA_HOST           = "127.0.0.1:$OLLAMA_CPU_PORT"
            CUDA_VISIBLE_DEVICES  = '-1'          # docs.ollama.com/gpu: id inválido = CPU
            OLLAMA_CONTEXT_LENGTH = "$OLLAMA_CTX"
            OLLAMA_KEEP_ALIVE     = '30m'
            OLLAMA_NUM_PARALLEL   = '1'
        }
        $userModels = [Environment]::GetEnvironmentVariable('OLLAMA_MODELS', 'User')
        if ($userModels) { $vars.OLLAMA_MODELS = $userModels }   # mismos modelos, sin duplicar
        foreach ($k in $vars.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k, 'Process')
                                     [Environment]::SetEnvironmentVariable($k, $vars[$k], 'Process') }
        try {
            $log = Join-Path $STATE_DIR 'ollama-cpu.log'
            $p = Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden -PassThru `
                               -RedirectStandardError $log -RedirectStandardOutput "$log.out"
        } finally {
            foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k], 'Process') }
        }
        Set-Content $pidFile $p.Id
        $ok = $false
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep 1
            try { $null = Invoke-RestMethod "http://127.0.0.1:$OLLAMA_CPU_PORT/api/version" -TimeoutSec 2; $ok = $true; break } catch {}
        }
        if (-not $ok) { throw "La instancia CPU no arrancó; mira $log" }
        Write-Ok "PID $($p.Id) · log $log"
    }
    $tags = Invoke-RestMethod "http://127.0.0.1:$OLLAMA_CPU_PORT/api/tags" -TimeoutSec 10
    $names = @($tags.models | ForEach-Object { $_.name })
    if ($names -notcontains $LLM_MODEL -and $names -notcontains "${LLM_MODEL}:latest") {
        Write-Warn2 "El modelo $LLM_MODEL no está descargado. Modelos disponibles: $($names -join ', ')"
        $ans = Read-Host "¿Descargarlo ahora con 'ollama pull $LLM_MODEL' (~9 GB para 14b)? [s/N]"
        if ($ans -match '^[sSyY]') {
            $env:OLLAMA_HOST = "127.0.0.1:$OLLAMA_CPU_PORT"
            try { & ollama pull $LLM_MODEL } finally { Remove-Item Env:OLLAMA_HOST }
        } else { Write-Warn2 'Elige uno existente: .\01-instalar.ps1 -Modelo <nombre> -Forzar -SinConstruir' }
    } else { Write-Ok "Modelo $LLM_MODEL disponible" }
}

Write-Step 'OpenShorts (docker compose up -d)'
Push-Location $OS_DIR
try {
    & docker compose up -d
    if ($LASTEXITCODE -ne 0) { throw 'docker compose up falló' }
} finally { Pop-Location }
$ok = $false
for ($i = 0; $i -lt 90; $i++) {
    try { $null = Invoke-RestMethod "http://localhost:$PORT_BACKEND/health" -TimeoutSec 3; $ok = $true; break } catch { Start-Sleep 2 }
}
if (-not $ok) { throw "El backend no responde en :$PORT_BACKEND; docker logs openshorts-backend" }
Write-Ok "API      http://localhost:$PORT_BACKEND  (docs: /docs, MCP: /mcp)"
Write-Ok "Panel    http://localhost:$PORT_FRONTEND"
