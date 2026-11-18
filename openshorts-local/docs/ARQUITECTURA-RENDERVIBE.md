# Propuesta: integración futura con RenderVibe Lab (Miniserie Studio)

Estado: **propuesta, nada implementado en RenderVibe**. No se ha leído el código
de `Miniserie-Studio`, que está en tu PC y no era accesible desde esta sesión. Lo
que sigue se basa en tus skills `rendervibe-*`, leídas en modo solo lectura:
puertos Studio **8295**, ComfyUI **8189**, Ollama **11434**; `.venv` propio;
`backend.launcher.busy_reason()`; `COORDINACION.md` y `colaboracion/`;
`backups/`; salida probada Full HD H.264 a 24 fps; `montaje/` y
`publicacion/<episodio>/`. Hay que contrastarlo con el informe de `00-auditar.ps1`.

## Principio: integración por contrato de archivos, no por código compartido

```
RenderVibe Lab (:8295, .venv propio)          openshorts-local (venv propio)        OpenShorts (Docker)
──────────────────────────────────────        ────────────────────────────          ───────────────────
script-to-shots ─> ComfyUI (:8189) renders
assemble-episode ─> montaje/ep03.mp4 ───────> rvshorts pipeline ep03 montaje/ep03.mp4 ──> /api/uploads, /api/process
                                               │  (guarda: no lanza si ComfyUI tiene cola)    whisper GPU · Ollama CPU :11435
                                               ├─ clips/ meta/ exports/ review.json
publish-plan (borradores) <── publish_queue.json (solo aprobados, published=false)
clip-review (QA) <──────────── clips/ + meta/clip_NN.md
```

- **Sin imports cruzados**: RenderVibe nunca importa `rvshorts` ni OpenShorts.
  Solo invoca la CLI o lee JSON. Cada uno conserva su Python (`.venv` de RenderVibe,
  `venv-rvshorts`, contenedor de OpenShorts): no hay conflictos de dependencias.
- **Puertos sin solapes**: 8295 (Studio), 8189/8188 (ComfyUI), 11434 (Ollama
  principal), 11435 (Ollama CPU del kit), 8000/5175/3100 (OpenShorts, o los
  alternativos que elija el instalador). El instalador nunca toma 8188/8189/8295/11434/11435.
- **GPU**: ComfyUI tiene prioridad. `rvshorts procesar` consulta `/queue` de ComfyUI y
  no lanza si hay renders en curso o pendientes (salvo `--ignorar-cola`). Whisper
  large-v3-turbo usa poca VRAM comparado con Wan/LTX, pero no se arriesgan los renders.
- **LLM**: RenderVibe sigue con su Ollama de :11434 (asistente de prompts); el kit
  usa :11435 en CPU. Los dos leen los mismos modelos.

## Fases propuestas (cada una requiere tu autorización)

1. **Lectura**: con el informe de `00-auditar.ps1` (árbol, manifiestos, puertos
   citados, nombres de variables `.env`, `busy_reason`), confirmar los puertos y la
   estructura de `montaje/` y `publicacion/`. Anotar el traspaso en
   `colaboracion/` según `rendervibe-relay:handoff-notes`, sin tocar `backend/`, `src/` ni `dist/`.
2. **Integración por CLI (sin tocar RenderVibe)**: un `.ps1` en `openshorts-local`
   que tome el último `montaje/*.mp4` de un episodio y ejecute `rvshorts pipeline`
   con el nombre del episodio. Los resultados quedan en `OpenShortsProyectos\<episodio>`.
3. **Lectura desde RenderVibe** (sí modifica RenderVibe: con copia en `backups/` y
   pruebas en su `.venv`): un panel "Shorts" que lea `review.json` y
   `meta/clip_NN.md` y llame a `rvshorts aprobar/rechazar`. Nada más.
4. **Publicación**: `rendervibe-publisher:publish-plan` lee `publish_queue.json`
   para redactar borradores. Publicar de verdad (MCP `publish_clip` o subida manual)
   sigue necesitando tu sí explícito por clip y plataforma.
5. **ComfyUI opcional**: miniaturas o *b-roll* generadas con plantillas de
   RenderVibe a partir de `meta/clip_NN.json`. Solo cuando la cola esté libre y a petición.

## Riesgos y cómo se evitan

| Riesgo | Mitigación |
|---|---|
| VRAM saturada durante un render | guarda de cola de ComfyUI; Ollama del kit en CPU; `ASR_GPU_CONCURRENCY=1` |
| Choque de puertos | lista de puertos reservados en `config.ps1`; el instalador elige libres |
| Mezcla de entornos Python | tres entornos separados; el kit nunca usa el `.venv` de RenderVibe salvo para `busy_reason()` con `-B` (sin `.pyc`) |
| Dos asistentes editando lo mismo | solo se escribe en `openshorts-local`, `C:\AI\openshorts` y `OpenShortsProyectos` |
| Publicación accidental | `rvshorts` no tiene comando de publicar; `publish_queue.json` con `published: false` |
