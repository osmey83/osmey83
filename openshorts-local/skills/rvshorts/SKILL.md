---
name: rvshorts
description: Produce vertical clips (YouTube Shorts, TikTok, Reels) from LOCAL videos with the self-hosted OpenShorts + local Ollama + FFmpeg pipeline, organised per project with manual approval. Use when the user wants to cut a local video into shorts, generate hooks/titles/descriptions/hashtags with the local LLM, analyse a script's narrative and retention, export H.264/H.265, or review/approve clips. Never publishes.
---

# rvshorts: clips verticales locales con aprobación manual

CLI: `%LOCALAPPDATA%\openshorts-local\rvshorts.cmd` (en adelante `rvshorts`;
ya lleva el puerto, el modelo y la carpeta de este equipo).
Proyectos en `D:\AI\OpenShortsProyectos\<proyecto>`.

## Antes de empezar
1. `rvshorts diagnostico` — OpenShorts, Ollama CPU (:11435) y encoders. Si falla,
   pide al usuario ejecutar `D:\Comfy Desktop\openshorts-local\windows\iniciar.ps1`.
2. El video debe durar **más de 45 s** (OpenShorts lo exige) y el usuario debe
   tener derechos sobre él: `procesar` lo declara (`acknowledged: true`).

## Flujo
```
rvshorts pipeline "<proyecto>" "<ruta\video.mp4 | https://youtube.com/watch?v=...>" [--clips N] [--codec h265]
```
equivale a `nuevo` → `procesar` → `descargar` → `enriquecer` → `exportar` y
termina en `revisar`. Pasos sueltos:

| Comando | Qué hace |
|---|---|
| `nuevo <p> <video> [--copiar]` | crea la carpeta del proyecto |
| `procesar <p> [--clips N --min 15 --max 60 --layouts auto] [--esperar]` | sube y lanza el trabajo en OpenShorts |
| `descargar <p>` | espera, baja los clips 9:16 y sus transcripciones |
| `enriquecer <p> [--idioma es] [--clip i]` | ganchos puntuados, títulos, descripciones, hashtags, segmentación narrativa y análisis (Ollama CPU) |
| `exportar <p> --codec h264\|h265 [--motor auto\|gpu\|cpu]` | MP4 final con NVENC o CPU |
| `revisar <p>` | estado de cada clip y su mejor gancho |
| `aprobar <p> <i> [--nota]` / `rechazar` / `pendiente` | decisión manual |
| `analizar-guion <archivo.txt>` | estructura, ganchos y riesgos de retención de un guion |

Un trabajo tarda minutos (transcripción en GPU + LLM en CPU): lanza `pipeline`
en segundo plano si tu entorno lo permite y avisa al usuario.

## Reglas
- **Nunca publiques.** Los aprobados quedan en `publish_queue.json`
  (`"published": false`). Publicar (herramienta MCP `publish_clip` de
  OpenShorts o subida manual) requiere confirmación explícita del usuario
  por cada clip y plataforma.
- No apruebes clips por tu cuenta: muestra `meta\clip_XX.md` y deja que el
  usuario decida.
- Si `enriquecer` falla en un clip, los demás siguen; repite con `--clip i`.
- Las herramientas MCP `openshorts` (process_video, list_clips, recut_clip…)
  sirven para re-cortar (`recut_clip` usa segundos del vídeo ORIGINAL).
