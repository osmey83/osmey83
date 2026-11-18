"""Cliente de la API REST de una instancia OpenShorts autoalojada.

Rutas verificadas en el código de OpenShorts (app.py, commit c4b496a):
  GET  /health
  POST /api/uploads                     -> {upload_id, upload_url, max_mb}
  PUT  /api/uploads/{upload_id}         (bytes del video, en streaming)
  POST /api/process                     JSON {upload_id, acknowledged, ...}
  GET  /api/status/{job_id}             -> {status, logs, result}
  GET  /api/clip/{job_id}/{i}/transcript -> {captions, durationSec, language}
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional


class OpenShortsError(RuntimeError):
    pass


class OpenShortsClient:
    def __init__(self, base_url: str = "http://localhost:8000",
                 api_key: Optional[str] = None, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    # -- HTTP ------------------------------------------------------------
    def _headers(self, extra: Optional[dict] = None) -> dict:
        h = {"Accept": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        h.update(extra or {})
        return h

    def _request(self, method: str, path: str, body: Any = None,
                 headers: Optional[dict] = None, timeout: Optional[float] = None) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        data = None
        hdrs = self._headers(headers)
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        elif body is not None:
            data = body
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise OpenShortsError(f"{method} {path} -> HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise OpenShortsError(
                f"OpenShorts no responde en {self.base_url} ({e}). "
                "¿Está arrancado? (windows\\iniciar.ps1)") from e
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {"raw": raw}

    # -- API -------------------------------------------------------------
    def health(self) -> Any:
        return self._request("GET", "/health", timeout=10)

    def upload(self, video_path: str) -> str:
        """Sube un archivo local en streaming y devuelve su upload_id."""
        if not os.path.isfile(video_path):
            raise OpenShortsError(f"No existe el archivo: {video_path}")
        slot = self._request("POST", "/api/uploads",
                             {"filename": os.path.basename(video_path)})
        size = os.path.getsize(video_path)
        max_mb = slot.get("max_mb")
        if max_mb and size > int(max_mb) * 1024 * 1024:
            raise OpenShortsError(f"El video pesa {size / 2**20:.0f} MB y el límite de "
                                  f"la instancia es {max_mb} MB")
        # La URL que devuelve el servidor puede usar el host interno de Docker;
        # se usa solo la ruta, contra la base_url que sí alcanzamos.
        path = f"/api/uploads/{slot['upload_id']}"
        with open(video_path, "rb") as fh:
            self._request("PUT", path, body=fh, timeout=3600, headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(size),
            })
        return slot["upload_id"]

    def process(self, upload_id: str, *, target_clips: Optional[int] = None,
                clip_min: int = 15, clip_max: int = 60, layouts: Optional[list] = None,
                captions: bool = True, auto_hook: bool = True) -> str:
        body: dict = {"upload_id": upload_id, "acknowledged": True,
                      "output_format": "vertical",
                      "clip_min_seconds": clip_min, "clip_max_seconds": clip_max,
                      "captions": captions, "auto_hook": auto_hook}
        if target_clips:
            body["target_clips"] = int(target_clips)
        if layouts:
            body["layouts"] = layouts
        resp = self._request("POST", "/api/process", body, timeout=120)
        if resp.get("status") == "needs_confirmation":
            raise OpenShortsError("OpenShorts pide confirmación de baja calidad: "
                                  + json.dumps(resp, ensure_ascii=False)[:300])
        job_id = resp.get("job_id")
        if not job_id:
            raise OpenShortsError(f"Respuesta sin job_id: {resp}")
        return job_id

    def status(self, job_id: str) -> dict:
        return self._request("GET", f"/api/status/{job_id}")

    def wait(self, job_id: str, poll: float = 15.0, timeout: float = 4 * 3600,
             on_log: Optional[Callable[[str], None]] = None) -> dict:
        seen = 0
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = self.status(job_id)
            logs = st.get("logs") or []
            if on_log:
                for line in logs[seen:]:
                    on_log(str(line))
            seen = len(logs)
            if st.get("status") == "completed":
                return st
            if st.get("status") == "failed":
                tail = " | ".join(str(x) for x in logs[-5:])
                raise OpenShortsError(f"El trabajo {job_id} falló: {tail}")
            time.sleep(poll)
        raise OpenShortsError(f"Tiempo agotado esperando el trabajo {job_id}")

    def clip_transcript(self, job_id: str, index: int) -> dict:
        return self._request("GET", f"/api/clip/{job_id}/{index}/transcript")

    def download(self, url_or_path: str, dest: str) -> str:
        url = url_or_path if url_or_path.startswith("http") else f"{self.base_url}{url_or_path}"
        req = urllib.request.Request(url, headers=self._headers({"Accept": "*/*"}))
        tmp = dest + ".part"
        try:
            with urllib.request.urlopen(req, timeout=600) as resp, open(tmp, "wb") as out:
                shutil.copyfileobj(resp, out, 1024 * 1024)
        except urllib.error.URLError as e:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise OpenShortsError(f"No se pudo descargar {url}: {e}") from e
        os.replace(tmp, dest)
        return dest
