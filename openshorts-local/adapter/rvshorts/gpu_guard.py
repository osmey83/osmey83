"""No competir por la GPU con renders en curso.

OpenShorts transcribe y codifica en la RTX 5090; ComfyUI (RenderVibe Lab) usa
la misma VRAM para Wan/LTX. Antes de lanzar un trabajo, se consulta la cola de
ComfyUI (GET /queue -> queue_running / queue_pending). Un ComfyUI apagado no
bloquea nada.
"""
from __future__ import annotations

import json
import os
import urllib.request

DEFAULT_URLS = "http://127.0.0.1:8189,http://127.0.0.1:8188"


def comfy_busy(urls: str | None = None, timeout: float = 3.0) -> list[str]:
    """Lista de avisos 'url: N en curso, M pendientes' para cada ComfyUI ocupado."""
    busy = []
    for base in (urls or os.environ.get("RVSHORTS_COMFY_URLS") or DEFAULT_URLS).split(","):
        base = base.strip().rstrip("/")
        if not base:
            continue
        try:
            with urllib.request.urlopen(f"{base}/queue", timeout=timeout) as resp:
                q = json.loads(resp.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 - apagado o no es ComfyUI: no bloquea
            continue
        running, pending = len(q.get("queue_running") or []), len(q.get("queue_pending") or [])
        if running or pending:
            busy.append(f"{base}: {running} en curso, {pending} pendientes")
    return busy
