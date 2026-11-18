<#
.SYNOPSIS
  Registra el servidor MCP de OpenShorts en Claude Code (ámbito usuario) e
  instala las skills 'openshorts' (oficial del repo) y 'rvshorts' (del kit).
  Comando verificado en el README de OpenShorts:
    claude mcp add --transport http openshorts http://localhost:8000/mcp
#>
param([ValidateSet('user', 'local', 'project')][string]$Scope = 'user')
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\config.ps1"
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { throw 'No se encuentra el CLI de Claude Code (claude).' }

Write-Step 'Servidor MCP'
$url = "http://localhost:$PORT_BACKEND/mcp"
$list = (& claude mcp list 2>&1 | Out-String)
if ($list -match '(?m)^openshorts:') {
    Write-Ok 'Ya existe una entrada "openshorts":'
    Write-Host ($list -split "`n" | Where-Object { $_ -match '^openshorts:' })
    Write-Host "  Si apunta a otra URL: claude mcp remove openshorts -s $Scope  y repite."
} else {
    & claude mcp add --transport http --scope $Scope openshorts $url
    if ($LASTEXITCODE -ne 0) { throw 'claude mcp add falló' }
    Write-Ok "Registrado openshorts -> $url (scope $Scope)"
}

Write-Step 'Skills'
$skillsDir = Join-Path $env:USERPROFILE '.claude\skills'
New-Item -ItemType Directory -Force -Path $skillsDir | Out-Null
$pairs = @(
    @{ src = Join-Path $OS_DIR 'skills\openshorts'; dst = Join-Path $skillsDir 'openshorts' },
    @{ src = Join-Path $KIT_DIR 'skills\rvshorts';  dst = Join-Path $skillsDir 'rvshorts' }
)
foreach ($s in $pairs) {
    if (Test-Path $s.dst) { Write-Warn2 "$($s.dst) ya existe; no se sobrescribe." }
    elseif (Test-Path $s.src) { Copy-Item $s.src $s.dst -Recurse; Write-Ok "Skill instalada: $($s.dst)" }
    else { Write-Warn2 "No se encontró $($s.src)" }
}

Write-Step 'Comprobación'
& claude mcp list
Write-Host "`nEn Claude Code, /mcp muestra el estado; prueba: 'usa openshorts get_job_status de un job'."
