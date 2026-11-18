# OpenShorts local · RTX 5090 · Ollama (CPU) · MCP para Claude Code

Kit para instalar, configurar y validar [OpenShorts](https://github.com/mutonby/openshorts)
(MIT) en tu PC con Windows y conectarlo a Claude Code, a Ollama en CPU y a
FFmpeg/NVENC. Incluye `rvshorts`, un adaptador independiente que organiza el
trabajo por proyectos, añade el enriquecimiento editorial con Ollama y exige
aprobación manual. **No modifica el código de OpenShorts ni de RenderVibe Lab.**

> Estado: código auditado y probado en un contenedor Linux (detalle en
> [docs/INFORME.md](docs/INFORME.md)). La instalación en tu Windows **aún no
> se ha ejecutado**: los pasos de abajo la hacen y `03-validar.ps1` la
> comprueba de punta a punta.

## Arquitectura

```
Claude Code ──MCP (http://localhost:8000/mcp, 8 herramientas)──┐
     │                                                          │
     └─ skill rvshorts ─> rvshorts (venv propio) ──REST──> OpenShorts (Docker Desktop/WSL2, GPU)
                              │                             ├─ faster-whisper large-v3-turbo · CUDA (RTX 5090)
                              │                             ├─ PySceneDetect + YOLO/MediaPipe: reencuadre 9:16
                              │                             ├─ subtítulos palabra a palabra quemados
                              │                             ├─ h264_nvenc (FFMPEG_ENCODER=auto)
                              │                             └─ selector de momentos ──> Ollama CPU :11435
                              ├─ enriquecer ──────────────────────────────────────────> Ollama CPU :11435
                              ├─ exportar H.264/H.265 ──> FFmpeg del host (h264/hevc_nvenc o x264/x265)
                              ├─ guarda de VRAM ──> ComfyUI :8189/:8188 /queue (no lanza si hay renders)
                              └─ proyectos/<p>/ ... review.json ──> publish_queue.json (nunca publica)

Tu Ollama principal :11434, ComfyUI :8189 y RenderVibe Studio :8295 no se tocan.
```

**¿Por qué una segunda instancia de Ollama?** Pediste el LLM en CPU para dejar la
VRAM a ComfyUI y a Whisper. La documentación oficial de Ollama indica que un
`CUDA_VISIBLE_DEVICES` inválido (`-1`) fuerza CPU. Aplicarlo a tu Ollama
principal cambiaría el comportamiento de RenderVibe, así que `iniciar.ps1` arranca
otra instancia en `127.0.0.1:11435`, solo-CPU y con contexto de 16 384 tokens
(OpenShorts necesita más que el valor por defecto). Lee la misma carpeta de modelos:
no hay descargas duplicadas.

## Instalación por fases

Requisitos: Docker Desktop con backend WSL2, Git, Python 3.10+ (solo para el
adaptador, que usa la biblioteca estándar) y Ollama. Desde `openshorts-local\windows`:

```powershell
# 0. Auditoría SOLO LECTURA (versiones, driver, NVENC, puertos, ComfyUI, RenderVibe)
powershell -ExecutionPolicy Bypass -File .\00-auditar.ps1 -ProbarGpuDocker

# 1. Clona OpenShorts en C:\AI\openshorts (commit fijado), .env, override GPU, venv del adaptador, build
powershell -ExecutionPolicy Bypass -File .\01-instalar.ps1

# 2. Arranca Ollama-CPU (:11435) y los contenedores
powershell -ExecutionPolicy Bypass -File .\iniciar.ps1

# 3. Registra el MCP en Claude Code (scope user) e instala las skills
powershell -ExecutionPolicy Bypass -File .\02-registrar-mcp.ps1

# 4. Valida todo, incluido un pipeline real (o con tu video: -Video D:\...\charla.mp4)
powershell -ExecutionPolicy Bypass -File .\03-validar.ps1
```

Para detenerlo: `.\detener.ps1`. Hace `docker compose stop` (no borra
contenedores, imágenes ni clips) y para **solo** la instancia CPU que arrancó el kit.

Lo que el instalador **no** hace: no instala ni actualiza drivers, CUDA, PyTorch,
Ollama, ComfyUI ni Node; no cambia variables de entorno globales; no
sobrescribe `.env` ni el override sin `-Forzar`, y aun con `-Forzar` guarda antes
una copia `.bak-<fecha>`. Si los puertos 8000/5175/3100 están ocupados, elige
otros libres (sin tocar 8188/8189/8295/11434/11435).

## Uso diario

```powershell
$rv = "$env:LOCALAPPDATA\openshorts-local\venv-rvshorts\Scripts\rvshorts.exe"
& $rv diagnostico
& $rv pipeline "Episodio 03" "D:\Grabaciones\ep03.mp4" --codec h265
& $rv revisar "Episodio 03"
& $rv aprobar "Episodio 03" 1 --nota "gancho fuerte"
& $rv analizar-guion "D:\Miniserie\guiones\ep04.txt"
```

O, en Claude Code: *"Saca 3 shorts de D:\Grabaciones\ep03.mp4 con rvshorts y
enséñame los ganchos"*. La skill `rvshorts` sabe usar la CLI y tiene prohibido
publicar o aprobar por su cuenta.

Cada proyecto queda así: `%USERPROFILE%\OpenShortsProyectos\<proyecto>\`
`clips\` (9:16 de OpenShorts) · `meta\clip_NN.md|.json` (ganchos, títulos,
descripciones, hashtags, narrativa, análisis) · `exports\` (H.264/H.265) ·
`review.json` · `publish_queue.json` (solo los aprobados, `"published": false`).

## Qué es nativo y qué añade el kit

| Función pedida | OpenShorts (nativo) | Kit (`rvshorts`) |
|---|---|---|
| Importar videos locales | Sí: subida en el panel, `POST /api/uploads`, MCP `create_upload` | Subida en streaming desde la CLI |
| Transcribir | Sí: faster-whisper (CUDA) o Parakeet, por palabra | — |
| Detectar segmentos interesantes | Sí: escenas + LLM (Ollama vía `LLM_BASE_URL`) | — |
| Clips de 15 a 60 s | Sí (`clip_min/max_seconds`, mínimo de fuente 45 s) | — |
| 9:16 automático | Sí: TRACK / GENERAL / SPLIT / SCREENCAST | Escalado de seguridad a 1080×1920 al exportar |
| Subtítulos dinámicos | Sí: quemados, estilo `karaoke` por palabra | — |
| Títulos y descripciones | Sí, con el LLM local (YouTube/TikTok/Instagram) | Títulos alternativos por plataforma |
| Hashtags | Dentro de las descripciones | Listas limpias por plataforma |
| Ganchos para retención | Sí: gancho superpuesto (*auto_hook*) | 3 variantes puntuadas 1-10 con su motivo |
| Segmentación narrativa / análisis de guion | No | Sí (`enriquecer`, `analizar-guion`) |
| Exportar H.264 | Sí (NVENC o x264) | Sí |
| Exportar H.265 | **No** | Sí (`hevc_nvenc` o `libx265`, `hvc1`) |
| Organizar por proyecto | No (carpetas por `job_id`) | Sí |
| Aprobación manual antes de publicar | Publicar es una acción explícita (Upload-Post) | Revisión pendiente/aprobado/rechazado y cola sin publicación |
| MCP / Skills / CLI / API | Sí: `/mcp`, `mcp_stdio.py`, skill `openshorts`, `pip install openshorts`, REST `/docs` | Skill `rvshorts` |
| No competir por la VRAM con ComfyUI | No | Sí: comprueba la cola de ComfyUI antes de procesar |

Necesitan una clave de Gemini (opcional): el selector automático de *layout* por
fotogramas, el detector de contenido en pantalla y los videos **sin voz**. Sin
clave, OpenShorts usa el reencuadre por seguimiento de cara, y un video mudo falla
con un mensaje claro. YouTube Studio (miniaturas y títulos), AI Shorts y el doblaje
necesitan Gemini, fal.ai o ElevenLabs: quedan fuera de un pipeline 100 % local.

## Modelos

| Uso | Modelo | Dónde | Motivo |
|---|---|---|---|
| Momentos, títulos y descripciones (OpenShorts) + enriquecimiento | `qwen2.5:14b` | Ollama CPU :11435 | Lo recomienda el README de OpenShorts; JSON fiable (según OpenShorts, los 7-8B van bien y los 3B no) |
| Alternativa más rápida | `llama3.1:8b` (el valor por defecto de OpenShorts) | Ollama CPU | Si el 14B va lento en tu CPU: `01-instalar.ps1 -Modelo llama3.1:8b -Forzar -SinConstruir` |
| Transcripción | `large-v3-turbo` fp16 | RTX 5090 (CUDA) | Configuración GPU del README; `parakeet` es opcional y más rápido en idiomas europeos |
| Seguimiento / escenas | YOLOv8, MediaPipe, TransNetV2 | Imagen de OpenShorts | Los descarga la propia imagen |

`iniciar.ps1` pregunta antes de descargar un modelo que falte. No se borra nada.

## Compatibilidad con la RTX 5090 (Blackwell, sm_120)

- **Ollama**: la RTX 5090 (compute 12.0) aparece como soportada en docs.ollama.com/gpu. Aquí se usa en CPU por decisión tuya.
- **Imagen GPU de OpenShorts**: torch 2.11 + `cuda-toolkit 13.0.2`. CUDA 13.x exige **driver R580 o superior** (notas oficiales de NVIDIA). `00-auditar.ps1` lo comprueba; no se actualiza nada solo.
- **Whisper**: CTranslate2 4.8.5 con cuBLAS/cuDNN de CUDA 12. `03-validar.ps1` ejecuta un kernel real en la GPU. Si fallara en Blackwell, OpenShorts reintenta en CPU (lo documenta el README) y lo verías en la validación.
- **NVENC**: `h264_nvenc`/`hevc_nvenc` se prueban con una codificación real dentro del contenedor y en el FFmpeg del host.

## Seguridad

- OpenShorts se fija a un commit concreto (`c4b496a`), cuyo código y dependencias se revisaron (los *locks* fijan versiones exactas y las imágenes base van fijadas por digest).
- No se ejecuta ningún `curl | sh` ni script remoto. El registro MCP usa el comando que documenta el README de OpenShorts.
- La instancia Ollama del kit solo escucha en `127.0.0.1`.
- La auditoría de RenderVibe es de solo lectura: lista estructura y manifiestos, recoge solo **nombres** de variables `.env` (nunca valores) y llama a `busy_reason()` con `python -B`, que no escribe `.pyc`.

Más: [docs/INFORME.md](docs/INFORME.md) (pruebas, versiones, pendientes) ·
[docs/ARQUITECTURA-RENDERVIBE.md](docs/ARQUITECTURA-RENDERVIBE.md) (integración futura).
