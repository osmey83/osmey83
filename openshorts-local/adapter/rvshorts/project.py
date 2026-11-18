"""Organización por proyecto y aprobación manual.

<raíz>/<proyecto>/
  project.json            origen, trabajos de OpenShorts, ajustes
  source/                 copia opcional del video original
  openshorts/<job>.json   resultado crudo de OpenShorts
  clips/clip_00.mp4       clips 9:16 renderizados por OpenShorts
  meta/clip_00.json|.md   títulos, descripciones, hashtags, ganchos (Ollama)
  exports/                MP4 finales H.264/H.265
  review.json             estado de aprobación de cada clip
  publish_queue.json      SOLO los aprobados; nada se publica automáticamente
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from typing import Any, Optional

STATUSES = ("pendiente", "aprobado", "rechazado")
_CLIP_RE = re.compile(r"clip_\d{2}\.mp4")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", name.strip(), flags=re.UNICODE).strip("-").lower()
    if not s:
        raise ValueError("Nombre de proyecto vacío")
    return s[:80]


def _write_json(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Project:
    SUBDIRS = ("source", "openshorts", "clips", "meta", "exports")

    def __init__(self, root: str, name: str):
        self.slug = slugify(name)
        self.dir = os.path.join(root, self.slug)

    # -- creación / carga -------------------------------------------------
    @classmethod
    def create(cls, root: str, name: str, source_video: str, copy: bool = False) -> "Project":
        is_url = source_video.lower().startswith(("http://", "https://"))
        if not is_url and not os.path.isfile(source_video):
            raise FileNotFoundError(f"No existe el video: {source_video}")
        p = cls(root, name)
        for sub in cls.SUBDIRS:
            os.makedirs(os.path.join(p.dir, sub), exist_ok=True)
        if is_url:   # YouTube u otro enlace: lo descarga OpenShorts (yt-dlp)
            data = p.load() or {"name": name, "slug": p.slug, "created": _now(), "jobs": []}
            data["source"] = {"url": source_video}
            p.save(data)
            return p
        src = os.path.abspath(source_video)
        if copy:
            dest = os.path.join(p.dir, "source", os.path.basename(src))
            if not os.path.exists(dest):
                shutil.copy2(src, dest)
            src = dest
        data = p.load() or {"name": name, "slug": p.slug, "created": _now(), "jobs": []}
        data["source"] = {"path": src, "bytes": os.path.getsize(src)}
        p.save(data)
        return p

    @property
    def manifest(self) -> str:
        return os.path.join(self.dir, "project.json")

    def exists(self) -> bool:
        return os.path.exists(self.manifest)

    def load(self) -> Optional[dict]:
        return _read_json(self.manifest, None)

    def save(self, data: dict) -> None:
        os.makedirs(self.dir, exist_ok=True)
        _write_json(self.manifest, data)

    def require(self) -> dict:
        data = self.load()
        if not data:
            raise FileNotFoundError(f"El proyecto '{self.slug}' no existe en {self.dir}")
        return data

    def path(self, *parts: str) -> str:
        return os.path.join(self.dir, *parts)

    # -- trabajos ---------------------------------------------------------
    def add_job(self, job_id: str, options: dict) -> None:
        data = self.require()
        data["jobs"].append({"job_id": job_id, "submitted": _now(), "options": options})
        self.save(data)

    def last_job(self) -> str:
        jobs = self.require().get("jobs") or []
        if not jobs:
            raise RuntimeError("El proyecto no tiene trabajos de OpenShorts todavía")
        return jobs[-1]["job_id"]

    def save_result(self, job_id: str, status: dict) -> str:
        path = self.path("openshorts", f"{job_id}.json")
        _write_json(path, status)
        return path

    def load_result(self, job_id: Optional[str] = None) -> dict:
        job_id = job_id or self.last_job()
        return _read_json(self.path("openshorts", f"{job_id}.json"), {})

    def clip_files(self) -> list[str]:
        d = self.path("clips")
        return sorted(os.path.join(d, f) for f in os.listdir(d) if _CLIP_RE.fullmatch(f))

    def begin_job_download(self, job_id: str) -> Optional[str]:
        """Prepara clips/meta/exports para los resultados de ``job_id``.

        Si ahí hay resultados de OTRO trabajo, se mueven (no se borran) a
        historial/<job anterior>/ junto con su revisión, para que una
        aprobación nunca quede pegada a un clip distinto. Devuelve esa ruta."""
        rv = self.review()
        old = rv.get("job_id")
        has_files = any(os.listdir(self.path(s)) for s in ("clips", "meta", "exports")
                        if os.path.isdir(self.path(s)))
        if old == job_id or (not has_files and not rv["clips"]):
            return None
        dest = self.path("historial", old or f"previo-{datetime.now():%Y%m%d-%H%M%S}")
        os.makedirs(dest, exist_ok=True)
        for name in ("clips", "meta", "exports", "review.json", "publish_queue.json"):
            if os.path.exists(self.path(name)):
                shutil.move(self.path(name), os.path.join(dest, name))
        for sub in ("clips", "meta", "exports"):
            os.makedirs(self.path(sub), exist_ok=True)
        return dest

    # -- revisión ----------------------------------------------------------
    @property
    def review_path(self) -> str:
        return self.path("review.json")

    def review(self) -> dict:
        return _read_json(self.review_path, {"clips": {}})

    def ensure_review_entries(self, n_clips: int, job_id: Optional[str] = None) -> None:
        rv = self.review()
        if job_id:
            rv["job_id"] = job_id
        for i in range(n_clips):
            rv["clips"].setdefault(str(i), {"status": "pendiente", "note": "", "at": _now()})
        _write_json(self.review_path, rv)

    def set_status(self, index: int, status: str, note: str = "") -> dict:
        if status not in STATUSES:
            raise ValueError(f"Estado inválido: {status}")
        rv = self.review()
        if str(index) not in rv["clips"]:
            raise KeyError(f"El clip {index} no existe en la revisión de '{self.slug}'")
        rv["clips"][str(index)] = {"status": status, "note": note, "at": _now()}
        _write_json(self.review_path, rv)
        self._write_publish_queue(rv)
        return rv

    def _write_publish_queue(self, rv: dict) -> None:
        queue = []
        for idx, entry in sorted(rv["clips"].items(), key=lambda kv: int(kv[0])):
            if entry["status"] != "aprobado":
                continue
            meta = _read_json(self.path("meta", f"clip_{int(idx):02d}.json"), {})
            exports = sorted(f for f in os.listdir(self.path("exports"))
                             if f.startswith(f"clip_{int(idx):02d}"))
            queue.append({"clip": int(idx), "approved_at": entry["at"], "note": entry["note"],
                          "clip_file": self.path("clips", f"clip_{int(idx):02d}.mp4"),
                          "exports": [self.path("exports", f) for f in exports],
                          "titles": meta.get("titles"), "descriptions": meta.get("descriptions"),
                          "hashtags": meta.get("hashtags")})
        _write_json(self.path("publish_queue.json"),
                    {"generated": _now(), "published": False, "items": queue})
