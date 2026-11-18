"""Exportación final H.264 / H.265 con NVENC (RTX) y respaldo por CPU.

OpenShorts entrega los clips en H.264 (h264_nvenc o libx264). H.265 no es una
opción nativa de OpenShorts: la añade este módulo volviendo a codificar el clip
ya renderizado. Cada encoder se prueba una vez con una codificación mínima
(igual que hace ffmpeg_utils.py en OpenShorts) antes de usarlo.
"""
from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
from typing import Optional

ENCODERS = {
    ("h264", "gpu"): ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq",
                      "-rc", "vbr", "-cq", "21", "-b:v", "0", "-profile:v", "high"],
    ("h264", "cpu"): ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
                      "-profile:v", "high"],
    ("h265", "gpu"): ["-c:v", "hevc_nvenc", "-preset", "p5", "-tune", "hq",
                      "-rc", "vbr", "-cq", "23", "-b:v", "0", "-tag:v", "hvc1"],
    ("h265", "cpu"): ["-c:v", "libx265", "-preset", "medium", "-crf", "22",
                      "-tag:v", "hvc1"],
}
_NAMES = {("h264", "gpu"): "h264_nvenc", ("h264", "cpu"): "libx264",
          ("h265", "gpu"): "hevc_nvenc", ("h265", "cpu"): "libx265"}


def ffmpeg_bin() -> str:
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("No se encuentra ffmpeg en el PATH (o define FFMPEG=ruta)")
    return exe


def ffprobe_bin() -> str:
    exe = os.environ.get("FFPROBE") or shutil.which("ffprobe")
    if not exe:
        raise RuntimeError("No se encuentra ffprobe en el PATH (o define FFPROBE=ruta)")
    return exe


@functools.lru_cache(maxsize=None)
def encoder_works(name: str) -> bool:
    """Codifica 1 fotograma sintético; True si el encoder funciona de verdad."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", "testsrc=size=256x256:rate=1", "-frames:v", "1", "-c:v", name,
           "-f", "null", "-"]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def pick_encoder(codec: str, prefer: str = "auto") -> tuple[str, list[str]]:
    if codec not in ("h264", "h265"):
        raise ValueError("codec debe ser h264 o h265")
    order = {"auto": ("gpu", "cpu"), "gpu": ("gpu",), "cpu": ("cpu",)}[prefer]
    for kind in order:
        name = _NAMES[(codec, kind)]
        if encoder_works(name):
            return name, ENCODERS[(codec, kind)]
    raise RuntimeError(f"Ningún encoder disponible para {codec} ({'/'.join(order)})")


def probe(path: str) -> dict:
    cmd = [ffprobe_bin(), "-v", "error", "-show_entries",
           "stream=codec_type,codec_name,width,height:format=duration",
           "-of", "json", path]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    return {"video_codec": v.get("codec_name"), "width": v.get("width"),
            "height": v.get("height"), "audio_codec": a.get("codec_name") if a else None,
            "duration": float(data.get("format", {}).get("duration") or 0)}


def export(src: str, dest: str, codec: str = "h264", prefer: str = "auto",
           size: Optional[tuple[int, int]] = (1080, 1920)) -> dict:
    """Recodifica ``src`` a MP4 listo para subir. Ajusta a 9:16 si hace falta
    (escala y rellena; el reencuadre inteligente ya lo hizo OpenShorts)."""
    name, vargs = pick_encoder(codec, prefer)
    vf = []
    if size:
        w, h = size
        vf = ["-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"]
    tmp = dest + ".part.mp4"
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-i", src,
           *vf, *vargs, "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-movflags", "+faststart", tmp]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError(f"ffmpeg falló con {name}: {res.stderr.strip()[-500:]}")
    os.replace(tmp, dest)
    info = probe(dest)
    info["encoder"] = name
    return info
