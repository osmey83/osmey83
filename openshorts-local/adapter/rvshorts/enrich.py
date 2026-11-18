"""Enriquecimiento editorial con un LLM local (Ollama en CPU).

OpenShorts ya genera, de forma nativa y con el mismo LLM local, el título y las
descripciones de TikTok/Instagram de cada clip. Este módulo añade lo que
OpenShorts no hace con un LLM local: variantes de gancho puntuadas por
retención, hashtags por plataforma, segmentación narrativa, análisis de guion
y títulos alternativos. La salida del modelo se valida y normaliza aquí: un
modelo local que se salta un campo falla con un mensaje claro y se reintenta.
"""
from __future__ import annotations

import re
from typing import Any

from .llm import LLMClient

PLATFORMS = ("youtube", "tiktok", "instagram")
TITLE_LIMITS = {"youtube": 100, "tiktok": 150, "instagram": 125}
HASHTAG_LIMITS = {"youtube": 5, "tiktok": 6, "instagram": 10}
BEATS = ("gancho", "contexto", "tension", "climax", "cierre", "cta")

SYSTEM = ("Eres editor jefe de vídeo vertical (YouTube Shorts, TikTok, Reels) "
          "experto en retención. Respondes SIEMPRE con un único objeto JSON válido, "
          "sin markdown ni comentarios, en el idioma indicado.")

CLIP_PROMPT = """Idioma de salida: {lang}.
Clip vertical de {dur:.1f} s. Título actual propuesto por OpenShorts: "{title}".
Transcripción con marcas de tiempo (segundos desde el inicio del clip):
{timed}

Devuelve exactamente este JSON:
{{
 "hooks": [{{"text": "<gancho de 3-12 palabras para los primeros 2 s>",
            "type": "<pregunta|promesa|contraste|dato|curiosidad>",
            "retention_score": <1-10>, "why": "<motivo breve>"}}],   // 3 variantes
 "titles": {{"youtube": "<máx 100 caracteres>", "tiktok": "...", "instagram": "..."}},
 "descriptions": {{"youtube": "...", "tiktok": "...", "instagram": "..."}},
 "hashtags": {{"youtube": ["#..."], "tiktok": ["#..."], "instagram": ["#..."]}},
 "narrative": [{{"beat": "<gancho|contexto|tension|climax|cierre|cta>",
                "start_s": <número>, "end_s": <número>, "summary": "..."}}],
 "analysis": {{"topic": "...", "emotion": "...", "clarity_score": <1-10>,
              "retention_risks": ["..."], "improvements": ["..."]}}
}}
Reglas: nada inventado que no esté en la transcripción; hashtags sin espacios;
tiempos dentro de 0..{dur:.1f}."""

SCRIPT_PROMPT = """Idioma de salida: {lang}.
Analiza este guion para un vídeo vertical de 15-60 s y propone su estructura.
GUION:
\"\"\"{script}\"\"\"

Devuelve exactamente este JSON:
{{
 "hooks": [{{"text": "...", "type": "...", "retention_score": <1-10>, "why": "..."}}],
 "titles": {{"youtube": "...", "tiktok": "...", "instagram": "..."}},
 "descriptions": {{"youtube": "...", "tiktok": "...", "instagram": "..."}},
 "hashtags": {{"youtube": ["#..."], "tiktok": ["#..."], "instagram": ["#..."]}},
 "narrative": [{{"beat": "<gancho|contexto|tension|climax|cierre|cta>",
                "start_s": <segundo estimado>, "end_s": <segundo estimado>,
                "summary": "...", "lines": "<frases del guion en este tramo>"}}],
 "analysis": {{"topic": "...", "emotion": "...", "clarity_score": <1-10>,
              "estimated_seconds": <número>, "retention_risks": ["..."],
              "improvements": ["..."], "viral_moments": ["<frase más compartible>"]}}
}}"""


# --- normalización ------------------------------------------------------

def _str(v: Any, field: str) -> str:
    if not isinstance(v, (str, int, float)) or not str(v).strip():
        raise ValueError(f"campo '{field}' vacío o no es texto")
    return str(v).strip()


def _score(v: Any, field: str) -> int:
    try:
        return max(1, min(10, int(round(float(v)))))
    except (TypeError, ValueError):
        raise ValueError(f"campo '{field}' debe ser un número 1-10") from None


def _hashtag(tag: str) -> str:
    tag = re.sub(r"[\s#]+", "", str(tag))
    tag = re.sub(r"[^\w]", "", tag, flags=re.UNICODE)
    return f"#{tag}" if tag else ""


def _per_platform(d: Any, field: str) -> dict:
    if not isinstance(d, dict):
        raise ValueError(f"'{field}' debe ser un objeto con youtube/tiktok/instagram")
    out = {}
    for p in PLATFORMS:
        val = d.get(p)
        if val is None:  # un modelo pequeño suele usar 'shorts' o 'reels'
            val = d.get({"youtube": "shorts", "instagram": "reels"}.get(p, p))
        out[p] = val
    return out


def normalize(data: Any, duration: float | None = None) -> dict:
    if not isinstance(data, dict):
        raise ValueError("la respuesta no es un objeto JSON")
    hooks_in = data.get("hooks")
    if not isinstance(hooks_in, list) or not hooks_in:
        raise ValueError("'hooks' debe ser una lista no vacía")
    hooks = [{"text": _str(h.get("text"), "hooks.text"),
              "type": str(h.get("type") or "").strip() or "curiosidad",
              "retention_score": _score(h.get("retention_score"), "hooks.retention_score"),
              "why": str(h.get("why") or "").strip()}
             for h in hooks_in if isinstance(h, dict)]
    hooks.sort(key=lambda h: h["retention_score"], reverse=True)

    titles = {p: _str(t, f"titles.{p}")[:TITLE_LIMITS[p]]
              for p, t in _per_platform(data.get("titles"), "titles").items()}
    descriptions = {p: _str(t, f"descriptions.{p}")
                    for p, t in _per_platform(data.get("descriptions"), "descriptions").items()}

    hashtags = {}
    for p, tags in _per_platform(data.get("hashtags"), "hashtags").items():
        if isinstance(tags, str):
            tags = tags.split()
        if not isinstance(tags, list):
            raise ValueError(f"'hashtags.{p}' debe ser una lista")
        seen, clean = set(), []
        for t in tags:
            h = _hashtag(t)
            if h and h.lower() not in seen:
                seen.add(h.lower())
                clean.append(h)
        if not clean:
            raise ValueError(f"'hashtags.{p}' está vacío")
        hashtags[p] = clean[:HASHTAG_LIMITS[p]]

    narrative = []
    for b in data.get("narrative") or []:
        if not isinstance(b, dict):
            continue
        beat = str(b.get("beat") or "").strip().lower()
        beat = beat.replace("tensión", "tension").replace("clímax", "climax")
        if beat not in BEATS:
            beat = "contexto"
        try:
            start, end = float(b.get("start_s", 0)), float(b.get("end_s", 0))
        except (TypeError, ValueError):
            start, end = 0.0, 0.0
        if duration:
            start, end = max(0.0, min(start, duration)), max(0.0, min(end, duration))
        if end < start:
            start, end = end, start
        item = {"beat": beat, "start_s": round(start, 2), "end_s": round(end, 2),
                "summary": str(b.get("summary") or "").strip()}
        if b.get("lines"):
            item["lines"] = str(b["lines"]).strip()
        narrative.append(item)
    if not narrative:
        raise ValueError("'narrative' debe tener al menos un tramo")
    narrative.sort(key=lambda b: b["start_s"])

    a = data.get("analysis")
    if not isinstance(a, dict):
        raise ValueError("'analysis' debe ser un objeto")
    analysis = {k: v for k, v in a.items()}
    analysis["clarity_score"] = _score(a.get("clarity_score"), "analysis.clarity_score")
    for key in ("retention_risks", "improvements", "viral_moments"):
        if key in analysis and not isinstance(analysis[key], list):
            analysis[key] = [str(analysis[key])]

    return {"hooks": hooks, "titles": titles, "descriptions": descriptions,
            "hashtags": hashtags, "narrative": narrative, "analysis": analysis}


# --- entradas -------------------------------------------------------------

LANGS = {"es": "español", "en": "inglés", "pt": "portugués", "fr": "francés",
         "it": "italiano", "de": "alemán"}


def timed_transcript(captions: list[dict], every: float = 4.0) -> str:
    """Agrupa palabras en líneas '[12.0s] texto' para que el modelo sitúe tramos."""
    lines, cur, cur_start = [], [], None
    for w in captions:
        t = w.get("startMs", 0) / 1000.0
        if cur_start is None:
            cur_start = t
        cur.append(str(w.get("text", "")).strip())
        if t - cur_start >= every:
            lines.append(f"[{cur_start:.1f}s] {' '.join(x for x in cur if x)}")
            cur, cur_start = [], None
    if cur:
        lines.append(f"[{(cur_start or 0):.1f}s] {' '.join(x for x in cur if x)}")
    return "\n".join(lines)


def enrich_clip(llm: LLMClient, transcript: dict, title: str = "",
                language: str | None = None) -> dict:
    captions = transcript.get("captions") or []
    if not captions:
        raise ValueError("El clip no tiene transcripción (¿video sin voz?)")
    dur = float(transcript.get("durationSec") or captions[-1].get("endMs", 0) / 1000.0)
    lang = LANGS.get((language or transcript.get("language") or "es")[:2], "español")
    prompt = CLIP_PROMPT.format(lang=lang, dur=dur, title=title or "(sin título)",
                                timed=timed_transcript(captions))
    return llm.chat_json(SYSTEM, prompt, validate=lambda d: normalize(d, dur))


def analyze_script(llm: LLMClient, script: str, language: str = "es") -> dict:
    script = script.strip()
    if len(script) < 20:
        raise ValueError("El guion es demasiado corto para analizarlo")
    prompt = SCRIPT_PROMPT.format(lang=LANGS.get(language[:2], "español"), script=script[:12000])
    return llm.chat_json(SYSTEM, prompt, validate=lambda d: normalize(d, None))


def to_markdown(meta: dict, header: str) -> str:
    out = [f"# {header}", ""]
    out.append("## Ganchos (ordenados por retención)")
    for h in meta["hooks"]:
        out.append(f"- **{h['retention_score']}/10** · {h['text']}  _({h['type']}: {h['why']})_")
    for section, key in (("Títulos", "titles"), ("Descripciones", "descriptions")):
        out += ["", f"## {section}"]
        out += [f"- **{p}**: {meta[key][p]}" for p in PLATFORMS]
    out += ["", "## Hashtags"]
    out += [f"- **{p}**: {' '.join(meta['hashtags'][p])}" for p in PLATFORMS]
    out += ["", "## Estructura narrativa"]
    out += [f"- `{b['start_s']:>5.1f}–{b['end_s']:>5.1f}s` **{b['beat']}**: {b['summary']}"
            for b in meta["narrative"]]
    a = meta["analysis"]
    out += ["", "## Análisis", f"- Tema: {a.get('topic', '')}", f"- Emoción: {a.get('emotion', '')}",
            f"- Claridad: {a['clarity_score']}/10"]
    for key, label in (("retention_risks", "Riesgos de retención"), ("improvements", "Mejoras"),
                       ("viral_moments", "Momentos virales")):
        if a.get(key):
            out.append(f"- {label}:")
            out += [f"  - {x}" for x in a[key]]
    return "\n".join(out) + "\n"
