"""rvshorts: proyectos de clips verticales sobre OpenShorts + Ollama + FFmpeg.

Flujo:  nuevo -> procesar -> descargar -> enriquecer -> exportar -> revisar
        -> aprobar/rechazar.   'pipeline' encadena todo hasta la revisión.
Nada se publica: los aprobados quedan en publish_queue.json para publicarlos a
mano (o, en el futuro, desde RenderVibe Lab).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from . import enrich as enrich_mod
from . import export as export_mod
from .gpu_guard import comfy_busy
from .llm import LLMClient, LLMError
from .openshorts_api import OpenShortsClient, OpenShortsError
from .project import Project, _read_json, _write_json

DEFAULTS = {
    "OPENSHORTS_URL": "http://localhost:8000",
    "RVSHORTS_LLM_URL": "http://127.0.0.1:11435/v1",   # instancia Ollama solo-CPU
    "RVSHORTS_LLM_MODEL": "qwen2.5:14b",
    "RVSHORTS_PROYECTOS": os.path.join(os.path.expanduser("~"), "OpenShortsProyectos"),
}


def env(key: str) -> str:
    return os.environ.get(key) or DEFAULTS[key]


def log(msg: str) -> None:
    print(msg, flush=True)


def _clients(args) -> tuple[OpenShortsClient, LLMClient]:
    os_client = OpenShortsClient(args.openshorts, api_key=os.environ.get("OPENSHORTS_API_KEY"))
    llm = LLMClient(args.llm_url, args.modelo,
                    timeout=float(os.environ.get("RVSHORTS_LLM_TIMEOUT") or 900))
    return os_client, llm


# --- comandos ------------------------------------------------------------

def cmd_diagnostico(args) -> int:
    os_client, llm = _clients(args)
    ok = True
    try:
        log(f"[OK] OpenShorts {args.openshorts}: {json.dumps(os_client.health())[:120]}")
    except OpenShortsError as e:
        ok = False
        log(f"[FALLO] OpenShorts: {e}")
    try:
        models = llm.ping()
        has = args.modelo in models or f"{args.modelo}:latest" in models
        log(f"[{'OK' if has else 'AVISO'}] Ollama {args.llm_url}: {len(models)} modelos; "
            f"'{args.modelo}' {'disponible' if has else 'NO está descargado'}")
        ok &= has
    except LLMError as e:
        ok = False
        log(f"[FALLO] {e}")
    try:
        for name in ("h264_nvenc", "hevc_nvenc", "libx264", "libx265"):
            log(f"[{'OK' if export_mod.encoder_works(name) else '--'}] encoder {name}")
    except RuntimeError as e:
        ok = False
        log(f"[FALLO] {e}")
    return 0 if ok else 1


def cmd_nuevo(args) -> int:
    p = Project.create(args.raiz, args.nombre, args.video, copy=args.copiar)
    log(f"Proyecto '{p.slug}' en {p.dir}")
    return 0


def cmd_procesar(args) -> int:
    os_client, _ = _clients(args)
    p = Project(args.raiz, args.nombre)
    src = p.require()["source"]["path"]
    busy = comfy_busy()
    if busy and not args.ignorar_cola:
        log("ComfyUI tiene renders activos; no se lanza OpenShorts para no competir por la VRAM:")
        for b in busy:
            log(f"  · {b}")
        log("Espera a que termine la cola o repite con --ignorar-cola.")
        return 3
    log(f"Subiendo {src} a OpenShorts…")
    upload_id = os_client.upload(src)
    opts = {"target_clips": args.clips, "clip_min": args.min, "clip_max": args.max,
            "layouts": args.layouts.split(",") if args.layouts else None,
            "captions": not args.sin_subtitulos, "auto_hook": not args.sin_gancho}
    job_id = os_client.process(upload_id, **opts)
    p.add_job(job_id, opts)
    log(f"Trabajo enviado: {job_id}")
    if args.esperar:
        return cmd_descargar(argparse.Namespace(**{**vars(args), "job": job_id}))
    return 0


def _local_clip_path(job_id: str, video_url: str) -> Optional[str]:
    root = os.environ.get("OPENSHORTS_OUTPUT_DIR")
    if not root or not video_url:
        return None
    cand = os.path.join(root, job_id, os.path.basename(video_url.split("?")[0]))
    return cand if os.path.isfile(cand) else None


def cmd_descargar(args) -> int:
    os_client, _ = _clients(args)
    p = Project(args.raiz, args.nombre)
    job_id = getattr(args, "job", None) or p.last_job()
    log(f"Esperando el trabajo {job_id} (puede tardar varios minutos)…")
    st = os_client.wait(job_id, poll=args.intervalo,
                        on_log=(lambda line: log(f"  · {line}")) if args.verbose else None)
    p.save_result(job_id, st)
    clips = (st.get("result") or {}).get("clips") or []
    if not clips:
        log("OpenShorts terminó sin clips.")
        return 1
    for i, clip in enumerate(clips):
        dest = p.path("clips", f"clip_{i:02d}.mp4")
        local = _local_clip_path(job_id, clip.get("video_url", ""))
        if local:
            import shutil
            shutil.copy2(local, dest)
        else:
            os_client.download(clip.get("download_url") or clip["video_url"], dest)
        tr = os_client.clip_transcript(job_id, i)
        _write_json(p.path("meta", f"clip_{i:02d}.transcript.json"), tr)
        log(f"  clip {i:02d}: {clip.get('title', '')!r} "
            f"({clip.get('start', 0):.1f}–{clip.get('end', 0):.1f}s)")
    p.ensure_review_entries(len(clips))
    log(f"{len(clips)} clips en {p.path('clips')}")
    return 0


def cmd_enriquecer(args) -> int:
    _, llm = _clients(args)
    p = Project(args.raiz, args.nombre)
    clips = (p.load_result().get("result") or {}).get("clips") or []
    failures = 0
    for i, clip in enumerate(clips):
        if args.clip is not None and i != args.clip:
            continue
        tr = _read_json(p.path("meta", f"clip_{i:02d}.transcript.json"), None)
        if not tr:
            log(f"  clip {i:02d}: sin transcripción, ¿ejecutaste 'descargar'?")
            failures += 1
            continue
        log(f"  clip {i:02d}: analizando con {args.modelo} (CPU, paciencia)…")
        try:
            meta = enrich_mod.enrich_clip(llm, tr, clip.get("title", ""), args.idioma)
        except (LLMError, ValueError) as e:
            log(f"  clip {i:02d}: ERROR {e}")
            failures += 1
            continue
        meta["openshorts"] = {k: clip.get(k) for k in (
            "title", "video_title_for_youtube_short", "video_description_for_tiktok",
            "video_description_for_instagram", "start", "end", "viral_score", "score")
            if clip.get(k) is not None}
        meta["model"] = args.modelo
        _write_json(p.path("meta", f"clip_{i:02d}.json"), meta)
        with open(p.path("meta", f"clip_{i:02d}.md"), "w", encoding="utf-8") as f:
            f.write(enrich_mod.to_markdown(meta, f"{p.slug} · clip {i:02d}"))
        log(f"  clip {i:02d}: gancho top → {meta['hooks'][0]['text']!r}")
    return 1 if failures else 0


def cmd_exportar(args) -> int:
    p = Project(args.raiz, args.nombre)
    files = p.clip_files()
    if not files:
        log("No hay clips descargados.")
        return 1
    for f in files:
        idx = int(os.path.basename(f)[5:7])
        if args.clip is not None and idx != args.clip:
            continue
        dest = p.path("exports", f"clip_{idx:02d}_{args.codec}.mp4")
        info = export_mod.export(f, dest, codec=args.codec, prefer=args.motor)
        log(f"  {os.path.basename(dest)}: {info['encoder']} {info['width']}x{info['height']} "
            f"{info['video_codec']}/{info['audio_codec']} {info['duration']:.1f}s")
    return 0


def cmd_revisar(args) -> int:
    p = Project(args.raiz, args.nombre)
    rv = p.review()["clips"]
    if not rv:
        log("Sin clips para revisar.")
        return 1
    for idx in sorted(rv, key=int):
        meta = _read_json(p.path("meta", f"clip_{int(idx):02d}.json"), {})
        title = (meta.get("titles") or {}).get("youtube", "(sin enriquecer)")
        log(f"  [{rv[idx]['status']:^10}] clip {int(idx):02d}  {title}")
        if meta:
            log(f"               gancho: {meta['hooks'][0]['text']}")
    log(f"Detalle: {p.path('meta')}  ·  aprobar: rvshorts aprobar {p.slug} <n>")
    return 0


def _set(args, status: str) -> int:
    p = Project(args.raiz, args.nombre)
    p.set_status(args.indice, status, args.nota or "")
    log(f"clip {args.indice:02d} → {status}. Cola de publicación: {p.path('publish_queue.json')}")
    return 0


def cmd_analizar_guion(args) -> int:
    _, llm = _clients(args)
    with open(args.archivo, encoding="utf-8") as f:
        script = f.read()
    meta = enrich_mod.analyze_script(llm, script, args.idioma or "es")
    base = args.salida or os.path.splitext(args.archivo)[0] + ".analisis"
    _write_json(base + ".json", meta)
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(enrich_mod.to_markdown(meta, f"Análisis de guion · {os.path.basename(args.archivo)}"))
    log(f"Análisis en {base}.md / .json")
    return 0


def cmd_pipeline(args) -> int:
    for step in (cmd_nuevo, cmd_procesar, cmd_descargar, cmd_enriquecer, cmd_exportar):
        rc = step(args)
        if rc != 0 and step is not cmd_enriquecer:
            return rc
    return cmd_revisar(args)


# --- argparse ------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rvshorts", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--openshorts", default=env("OPENSHORTS_URL"))
    ap.add_argument("--llm-url", default=env("RVSHORTS_LLM_URL"))
    ap.add_argument("--modelo", default=env("RVSHORTS_LLM_MODEL"))
    ap.add_argument("--raiz", default=env("RVSHORTS_PROYECTOS"), help="carpeta de proyectos")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("diagnostico").set_defaults(fn=cmd_diagnostico)

    def proj(name, fn, video=False):
        sp = sub.add_parser(name)
        sp.add_argument("nombre")
        if video:
            sp.add_argument("video")
            sp.add_argument("--copiar", action="store_true", help="copiar el original al proyecto")
        sp.set_defaults(fn=fn)
        return sp

    proj("nuevo", cmd_nuevo, video=True)

    def proc_opts(sp):
        sp.add_argument("--clips", type=int, default=None, help="objetivo 1-15 (vacío = IA decide)")
        sp.add_argument("--min", type=int, default=15)
        sp.add_argument("--max", type=int, default=60)
        sp.add_argument("--layouts", default="", help="p.ej. auto | split,screencast")
        sp.add_argument("--sin-subtitulos", action="store_true")
        sp.add_argument("--sin-gancho", action="store_true")
        sp.add_argument("--esperar", action="store_true")
        sp.add_argument("--ignorar-cola", action="store_true",
                        help="lanzar aunque ComfyUI tenga renders en cola")

    def dl_opts(sp):
        sp.add_argument("--job", default=None)
        sp.add_argument("--intervalo", type=float, default=15.0)
        sp.add_argument("--verbose", action="store_true")

    def en_opts(sp):
        sp.add_argument("--idioma", default=None, help="es, en… (por defecto, el del audio)")
        sp.add_argument("--clip", type=int, default=None)

    def ex_opts(sp, with_clip=True):
        sp.add_argument("--codec", choices=("h264", "h265"), default="h264")
        sp.add_argument("--motor", choices=("auto", "gpu", "cpu"), default="auto")
        if with_clip:
            sp.add_argument("--clip", type=int, default=None)

    proc_opts(proj("procesar", cmd_procesar)); dl_opts(sub.choices["procesar"])
    dl_opts(proj("descargar", cmd_descargar))
    en_opts(proj("enriquecer", cmd_enriquecer))
    ex_opts(proj("exportar", cmd_exportar))
    proj("revisar", cmd_revisar)
    for name, status in (("aprobar", "aprobado"), ("rechazar", "rechazado"),
                         ("pendiente", "pendiente")):
        sp = proj(name, lambda a, s=status: _set(a, s))
        sp.add_argument("indice", type=int)
        sp.add_argument("--nota", default="")

    sp = sub.add_parser("analizar-guion")
    sp.add_argument("archivo")
    sp.add_argument("--idioma", default="es")
    sp.add_argument("--salida", default=None)
    sp.set_defaults(fn=cmd_analizar_guion)

    sp = proj("pipeline", cmd_pipeline, video=True)
    proc_opts(sp); dl_opts(sp); en_opts(sp); ex_opts(sp, with_clip=False)
    sp.set_defaults(esperar=False, clip=None)
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # consola Windows
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (OpenShortsError, LLMError, FileNotFoundError, KeyError, ValueError,
            RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
