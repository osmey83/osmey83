"""Servidores HTTP falsos en el mismo proceso para probar el adaptador.

FakeOpenShorts reproduce las rutas y formas de respuesta de app.py de
OpenShorts (commit c4b496a); FakeLLM, el endpoint /v1 compatible con OpenAI
que expone Ollama.
"""
from __future__ import annotations

import json
import shutil
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Server:
    def __init__(self, handler_cls):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.httpd.owner = self
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _send(h, code, payload, ctype="application/json"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    h.send_response(code)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


class FakeOpenShorts(_Server):
    """clip_source: MP4 que se sirve como clip renderizado."""

    def __init__(self, clip_source: str, n_clips: int = 2, fail: bool = False):
        self.clip_source = clip_source
        self.n_clips = n_clips
        self.fail = fail
        self.uploads: dict[str, int] = {}
        self.jobs: dict[str, dict] = {}
        self.process_bodies: list[dict] = []
        super().__init__(_OSHandler)


class _OSHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    @property
    def s(self) -> FakeOpenShorts:
        return self.server.owner

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        p = self.path
        if p == "/health":
            return _send(self, 200, {"status": "ok"})
        if p.startswith("/api/status/"):
            job = self.s.jobs.get(p.rsplit("/", 1)[1])
            if not job:
                return _send(self, 404, {"detail": "Job not found"})
            job["polls"] += 1
            if job["polls"] < 2:
                return _send(self, 200, {"status": "processing", "logs": ["Transcribing…"],
                                         "result": None})
            if self.s.fail:
                return _send(self, 200, {"status": "failed",
                                         "logs": ["Transcribing…", "Error: CUDA out of memory"],
                                         "result": None})
            jid = p.rsplit("/", 1)[1]
            clips = [{"title": f"Momento {i}", "start": 10.0 * i, "end": 10.0 * i + 8,
                      "video_title_for_youtube_short": f"YT {i}",
                      "video_description_for_tiktok": "desc tt",
                      "video_description_for_instagram": "desc ig",
                      "video_url": f"/videos/{jid}/clip_{i}.mp4"}
                     for i in range(self.s.n_clips)]
            return _send(self, 200, {"status": "completed", "logs": ["done"],
                                     "result": {"clips": clips}})
        if p.startswith("/videos/"):
            with open(self.s.clip_source, "rb") as f:
                return _send(self, 200, f.read(), "video/mp4")
        if p.startswith("/api/clip/") and p.endswith("/transcript"):
            words = "hoy te cuento el error que casi arruina mi canal y cómo lo arreglé".split()
            caps = [{"text": w, "startMs": i * 600, "endMs": i * 600 + 500}
                    for i, w in enumerate(words)]
            return _send(self, 200, {"captions": caps, "durationSec": 8.0, "language": "es"})
        _send(self, 404, {"detail": "Not Found"})

    def do_POST(self):
        if self.path == "/api/uploads":
            self._json()
            uid = str(uuid.uuid4())
            self.s.uploads[uid] = 0
            return _send(self, 200, {"upload_id": uid,
                                     "upload_url": f"http://backend:8000/api/uploads/{uid}",
                                     "method": "PUT", "max_mb": 4096, "expires_in": 3600})
        if self.path == "/api/process":
            body = self._json()
            self.s.process_bodies.append(body)
            if not body.get("acknowledged"):
                return _send(self, 400, {"detail": "rights not acknowledged"})
            if body.get("upload_id") not in self.s.uploads:
                return _send(self, 404, {"detail": "Unknown or expired upload_id"})
            jid = str(uuid.uuid4())
            self.s.jobs[jid] = {"polls": 0}
            return _send(self, 200, {"job_id": jid, "status": "queued"})
        _send(self, 404, {"detail": "Not Found"})

    def do_PUT(self):
        uid = self.path.rsplit("/", 1)[1]
        if uid not in self.s.uploads:
            return _send(self, 404, {"detail": "Unknown or expired upload_id"})
        n = int(self.headers.get("Content-Length") or 0)
        remaining, got = n, 0
        while remaining:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            got += len(chunk)
            remaining -= len(chunk)
        self.s.uploads[uid] = got
        _send(self, 200, {"upload_id": uid, "bytes": got, "complete": True})


GOOD_META = {
    "hooks": [
        {"text": "Este error casi me cuesta el canal", "type": "contraste",
         "retention_score": 9, "why": "pérdida + curiosidad"},
        {"text": "¿Te ha pasado esto?", "type": "pregunta", "retention_score": "7", "why": "x"},
    ],
    "titles": {"youtube": "El error que casi arruina mi canal " + "x" * 120,
               "tiktok": "El error que casi arruina mi canal", "reels": "Mi peor error"},
    "descriptions": {"youtube": "d", "tiktok": "d", "instagram": "d"},
    "hashtags": {"youtube": ["#shorts", "youtube tips", "#Shorts"],
                 "tiktok": "#fyp #creadores", "instagram": ["#reels"]},
    "narrative": [{"beat": "tensión", "start_s": 3, "end_s": 6.5, "summary": "el error"},
                  {"beat": "gancho", "start_s": 0, "end_s": 3, "summary": "promesa"},
                  {"beat": "cierre", "start_s": 6.5, "end_s": 99, "summary": "solución"}],
    "analysis": {"topic": "errores de canal", "emotion": "alivio", "clarity_score": 8,
                 "retention_risks": "arranque lento", "improvements": ["cortar 1 s"]},
}


class FakeLLM(_Server):
    """replies: lista de contenidos a devolver en orden (el último se repite).
    reject_format: responde 400 si llega response_format (como un servidor viejo)."""

    def __init__(self, replies=None, reject_format=False):
        self.replies = list(replies or [json.dumps(GOOD_META)])
        self.reject_format = reject_format
        self.requests: list[dict] = []
        super().__init__(_LLMHandler)


class _LLMHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.endswith("/models"):
            return _send(self, 200, {"object": "list",
                                     "data": [{"id": "qwen2.5:7b-instruct"}, {"id": "llama3.1:8b"}]})
        _send(self, 404, {})

    def do_POST(self):
        s = self.server.owner
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        s.requests.append(body)
        if s.reject_format and "response_format" in body:
            return _send(self, 400, {"error": "unknown field response_format"})
        content = s.replies.pop(0) if len(s.replies) > 1 else s.replies[0]
        _send(self, 200, {"choices": [{"message": {"role": "assistant", "content": content}}],
                          "usage": {"prompt_tokens": 10, "completion_tokens": 5}})


def make_test_clip(path: str, w=1280, h=720, seconds=4) -> str:
    """Vídeo con audio generado por ffmpeg (horizontal, para probar el 9:16)."""
    import subprocess
    subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=30:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    path], check=True)
    return path


class FakeComfy(_Server):
    """GET /queue con N trabajos en curso y M pendientes."""

    def __init__(self, running=0, pending=0):
        self.running, self.pending = running, pending
        super().__init__(_ComfyHandler)


class _ComfyHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        s = self.server.owner
        if self.path == "/queue":
            return _send(self, 200, {"queue_running": [[0, "id"]] * s.running,
                                     "queue_pending": [[1, "id"]] * s.pending})
        _send(self, 404, {})
