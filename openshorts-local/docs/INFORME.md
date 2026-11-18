# Informe de auditoría, instalación y validación

Fecha: 2026-11-18 · Repositorio auditado: `mutonby/openshorts` @ `c4b496a73747664d9d0864b19c34085303e90d14`
(commit del 2026-11-17, licencia MIT).

## 1. Límite de esta sesión (léelo primero)

Esta sesión de Claude Code corre en un **contenedor Linux en la nube, sin GPU y
sin acceso a tu PC** (no había ningún equipo conectado; `list_computer_folders`
vino vacío y Desktop Commander también corría en el contenedor). Por eso:

- **No se ha instalado nada en tu Windows** ni se ha leído `Miniserie-Studio`.
- Lo marcado **Verificado** se ejecutó de verdad en el contenedor.
- Lo marcado **Pendiente (Windows)** lo comprueba `windows/03-validar.ps1` en tu equipo.
  No se declara funcional hasta que ese script lo confirme.
- Los scripts `.ps1` **no se han ejecutado ni validado sintácticamente**: instalar
  PowerShell en el contenedor no estaba permitido. Están escritos para Windows
  PowerShell 5.1 y guardados en UTF-8 con BOM, pero el primer uso real es en tu equipo.

## 2. Qué es OpenShorts (confirmado leyendo el código)

| Capacidad | Evidencia |
|---|---|
| Servidor MCP HTTP en `/mcp` y por stdio (`mcp_stdio.py`) | `mcp_server.py`, `mcp_stdio.py`; probado (ver §4) |
| 8 herramientas MCP: `process_video`, `create_upload`, `get_job_status`, `list_clips`, `get_quota`, `add_subtitles`, `recut_clip`, `publish_clip` | `tools/list` real |
| Skill oficial `skills/openshorts/SKILL.md` | presente en el repo |
| CLI `openshorts` (`cli/`, `pip install openshorts`) | `cli/pyproject.toml` |
| API REST + OpenAPI en `/docs` | `app.py` |
| LLM local compatible con OpenAI (Ollama) para los momentos virales y los títulos | `llm_backend.py`, `main.py:2162-2271`, `pro_mode.py:537` |
| Transcripción faster-whisper/Parakeet en CUDA, NVENC con `FFMPEG_ENCODER=auto` | `transcribe_backends.py`, `ffmpeg_utils.py` |
| Soporte Windows | vía **Docker Desktop + WSL2** (README §5). No hay instrucciones de instalación nativa |
| Duración mínima de la fuente: 45 s | respuesta real del backend (§4) |

## 3. Versiones

| Componente | Versión | Fuente |
|---|---|---|
| OpenShorts | commit `c4b496a` | `git log` |
| Imagen backend | `python:3.11-slim` (fijada por digest), FFmpeg de Debian trixie | `Dockerfile` |
| torch / torchvision | 2.11.0 / 0.26.0 (GPU: `cuda-toolkit 13.0.2`) | `requirements-gpu.lock` |
| faster-whisper / CTranslate2 | 1.2.1 / 4.8.5 (cuBLAS/cuDNN de CUDA 12) | lock |
| onnxruntime-gpu | 1.31.1 | lock |
| ultralytics / mediapipe | 8.4.46 / 0.10.14 | lock |
| MCP | servidor `openshorts` 1.0.0, protocolo `2025-06-18` | `initialize` real |
| rvshorts (kit) | 0.1.0, Python ≥ 3.10, sin dependencias | `adapter/pyproject.toml` |
| Node, Python, FFmpeg, Ollama, driver y Docker **de tu PC** | **Pendiente**: los recoge `00-auditar.ps1` | — |

Requisito verificado en la documentación de NVIDIA: CUDA 13.x necesita el
driver **R580 o superior**. La RTX 5090 (compute 12.0) figura como soportada en
docs.ollama.com/gpu.

## 4. Resultados de pruebas

| # | Prueba | Dónde | Resultado |
|---|---|---|---|
| 1 | Tests del propio OpenShorts: `llm_backend`, `mcp_protocol`, `mcp_stdio`, `mcp_endpoint`, `ffmpeg_utils`, `subtitles`, `burned_subtitles` | contenedor, deps del lock CPU | **121 OK, 2 omitidos** |
| 2 | MCP por stdio: `initialize` + `tools/list` sobre la app real | contenedor | **OK**: 8 herramientas |
| 3 | Backend real (uvicorn) `/health` | contenedor | **OK** |
| 4 | Subida en streaming (`POST /api/uploads` + `PUT`) | contenedor | **OK**: 5 597 258 bytes, idénticos al original |
| 5 | Error: fuente < 45 s | backend real | **OK**: HTTP 400 con mensaje claro, que el adaptador muestra (rc 2) |
| 6 | `/api/process` → cola → worker → `/api/status` | backend real | **OK hasta el worker**; el trabajo falla porque en el contenedor no hay torch/ultralytics (PyTorch, HuggingFace y las descargas de GitHub están bloqueados por la red). El adaptador informó del error con el log real (rc 2) |
| 7 | Adaptador: 22 tests (JSON del LLM, reintentos, *fallback* de `response_format`, normalización, proyecto/aprobación, exportación H.264/H.265, extremo a extremo con servidores simulados, guarda de ComfyUI, errores) | contenedor | **22 OK** |
| 8 | Exportación real H.265 del video de demostración | contenedor (libx265) | **OK**: 1080×1920, `hevc`/`hvc1`, AAC, 41,77 s; fotograma revisado a ojo (16:9 sin deformar) |
| 9 | Instalación, Docker GPU, torch `sm_120`, kernel Whisper en CUDA, ONNX CUDA | Windows | **Pendiente**: `03-validar.ps1` |
| 10 | NVENC h264/hevc (contenedor y host) | Windows | **Pendiente** |
| 11 | Ollama CPU: JSON real, `size_vram = 0`, contenedor → `host.docker.internal:11435` | Windows | **Pendiente** |
| 12 | MCP HTTP y registro en Claude Code | Windows | **Pendiente** |
| 13 | Pipeline completo con un video real (clips 9:16 de 15-60 s, transcripción, metadatos, H.265, revisión pendiente) | Windows | **Pendiente** |
| 14 | Calidad visual de los subtítulos dinámicos | Windows | **Pendiente**: revisión humana (no se puede comprobar por script) |

## 5. Funciones disponibles tras instalar (si la validación pasa)

Importación local, transcripción en GPU, detección de momentos con Ollama,
clips de 15-60 s en 9:16 con reencuadre inteligente, subtítulos dinámicos,
gancho superpuesto, títulos y descripciones por plataforma (nativo), más con
`rvshorts`: ganchos puntuados, hashtags por plataforma, segmentación narrativa,
análisis de guion, exportación H.264/H.265 con NVENC, proyectos, revisión
manual y cola de publicación sin publicar, y la guarda de VRAM frente a ComfyUI.
Desde Claude Code: MCP `openshorts` + skills `openshorts` y `rvshorts`.

## 6. Pendiente / fuera de alcance

1. **Ejecutar en tu Windows** `00` → `01` → `iniciar` → `02` → `03` y revisar el informe JSON.
2. Si el driver es < R580: la imagen GPU de OpenShorts (CUDA 13) no podrá usar torch en GPU. Actualizar el driver es decisión tuya; el kit no lo hace.
3. Rendimiento real del LLM en tu CPU (14B frente a 8B): lo mide `03-validar.ps1`.
4. Funciones de OpenShorts que necesitan la nube (Gemini/fal.ai/ElevenLabs/Upload-Post): selector visual de *layout*, videos sin voz, AI Shorts, YouTube Studio, doblaje y publicación. Son opcionales y no se han configurado.
5. Integración con RenderVibe Lab: diseñada (ver `ARQUITECTURA-RENDERVIBE.md`), **no implementada**. Necesita tu autorización y una lectura real del proyecto (la auditoría `00` la prepara).
