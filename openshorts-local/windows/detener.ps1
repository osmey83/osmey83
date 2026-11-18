<#
.SYNOPSIS
  Detiene los contenedores de OpenShorts (stop, no borra nada) y la instancia
  Ollama solo-CPU que arrancó iniciar.ps1. Tu Ollama principal sigue igual.
#>
$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\config.ps1"

Write-Step 'OpenShorts (docker compose stop)'
if (Test-Path $OS_DIR) {
    Push-Location $OS_DIR; & docker compose stop; Pop-Location
}

Write-Step "Ollama solo-CPU (:$OLLAMA_CPU_PORT)"
$pidFile = Join-Path $STATE_DIR 'ollama-cpu.pid'
if (Test-Path $pidFile) {
    $procId = [int](Get-Content $pidFile)
    $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
    # Solo se detiene si es ollama Y es quien escucha en el puerto del kit.
    $owner = Get-NetTCPConnection -State Listen -LocalPort $OLLAMA_CPU_PORT -ErrorAction SilentlyContinue |
             Select-Object -First 1
    if ($p -and $p.ProcessName -like 'ollama*' -and $owner -and $owner.OwningProcess -eq $procId) {
        # /T: también sus procesos runner hijos (liberan la RAM del modelo).
        & taskkill /PID $procId /T /F | Out-Null; Write-Ok "Detenido PID $procId y sus hijos"
    } else { Write-Warn2 "El PID $procId ya no es la instancia del kit; no se toca nada." }
    Remove-Item $pidFile
} else { Write-Host '  No había instancia arrancada por el kit.' }
