import json
import os
import shutil

import pytest

from rvshorts import enrich, export
from rvshorts.cli import main
from rvshorts.llm import LLMClient, LLMError, parse_json_text
from rvshorts.openshorts_api import OpenShortsClient, OpenShortsError
from rvshorts.project import Project

from fakes import GOOD_META, FakeLLM, FakeOpenShorts, make_test_clip

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg no instalado")


# --- normalización / JSON ------------------------------------------------

def test_parse_json_with_fences_and_prose():
    assert parse_json_text('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_text('Claro, aquí está: {"a": 2} ¡suerte!') == {"a": 2}


def test_normalize_cleans_model_output():
    m = enrich.normalize(GOOD_META, duration=8.0)
    assert [h["retention_score"] for h in m["hooks"]] == [9, 7]
    assert len(m["titles"]["youtube"]) == 100                 # límite de YouTube
    assert m["titles"]["instagram"] == "Mi peor error"        # alias 'reels'
    assert m["hashtags"]["youtube"] == ["#shorts", "#youtubetips"]  # sin duplicados
    assert m["hashtags"]["tiktok"] == ["#fyp", "#creadores"]
    assert [b["beat"] for b in m["narrative"]] == ["gancho", "tension", "cierre"]
    assert m["narrative"][-1]["end_s"] == 8.0                 # recortado a la duración
    assert m["analysis"]["retention_risks"] == ["arranque lento"]


@pytest.mark.parametrize("broken", [
    {},
    {**GOOD_META, "hooks": []},
    {**GOOD_META, "titles": {"youtube": "", "tiktok": "a", "instagram": "b"}},
    {**GOOD_META, "hashtags": {"youtube": [], "tiktok": ["#a"], "instagram": ["#b"]}},
    {**GOOD_META, "analysis": {"clarity_score": "alto"}},
])
def test_normalize_rejects_incomplete_output(broken):
    with pytest.raises(ValueError):
        enrich.normalize(broken, 8.0)


# --- cliente LLM -----------------------------------------------------------

def test_llm_retries_after_invalid_json_then_succeeds():
    srv = FakeLLM(replies=["no es json", json.dumps(GOOD_META)])
    try:
        llm = LLMClient(srv.url + "/v1", "qwen2.5:14b")
        out = llm.chat_json("s", "p", validate=lambda d: enrich.normalize(d, 8))
        assert out["hooks"][0]["retention_score"] == 9
        assert len(srv.requests) == 2
        assert "no era válida" in srv.requests[1]["messages"][-1]["content"]
    finally:
        srv.close()


def test_llm_falls_back_when_response_format_rejected():
    srv = FakeLLM(reject_format=True)
    try:
        out = LLMClient(srv.url + "/v1", "m").chat_json("s", "p")
        assert out["titles"]["tiktok"]
        assert "response_format" in srv.requests[0] and "response_format" not in srv.requests[1]
    finally:
        srv.close()


def test_llm_gives_up_with_clear_error():
    srv = FakeLLM(replies=["{}"])
    try:
        with pytest.raises(LLMError, match="no devolvió JSON válido"):
            LLMClient(srv.url + "/v1", "m").chat_json(
                "s", "p", validate=lambda d: enrich.normalize(d, 8), retries=1)
    finally:
        srv.close()


def test_llm_unreachable_is_reported():
    with pytest.raises(LLMError, match="no responde"):
        LLMClient("http://127.0.0.1:9/v1", "m").ping()


def test_openshorts_unreachable_is_reported():
    with pytest.raises(OpenShortsError, match="no responde"):
        OpenShortsClient("http://127.0.0.1:9").health()


# --- proyecto y aprobación -------------------------------------------------

def test_review_and_publish_queue(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 10)
    p = Project.create(str(tmp_path / "proj"), "Mi Proyecto Ñ", str(video))
    assert p.slug == "mi-proyecto-ñ"
    p.ensure_review_entries(3)
    with pytest.raises(KeyError):
        p.set_status(7, "aprobado")
    with pytest.raises(ValueError):
        p.set_status(0, "publicado")
    p.set_status(1, "aprobado", "buen gancho")
    p.set_status(2, "rechazado")
    q = json.load(open(p.path("publish_queue.json"), encoding="utf-8"))
    assert q["published"] is False
    assert [i["clip"] for i in q["items"]] == [1]


# --- exportación -----------------------------------------------------------

@needs_ffmpeg
@pytest.mark.parametrize("codec,vcodec", [("h264", "h264"), ("h265", "hevc")])
def test_export_to_vertical_mp4(tmp_path, codec, vcodec):
    src = make_test_clip(str(tmp_path / "in.mp4"))
    info = export.export(src, str(tmp_path / f"out_{codec}.mp4"), codec=codec, prefer="auto")
    assert (info["width"], info["height"]) == (1080, 1920)
    assert info["video_codec"] == vcodec and info["audio_codec"] == "aac"
    assert abs(info["duration"] - 4.0) < 0.2


@needs_ffmpeg
def test_export_gpu_only_fails_cleanly_without_nvenc(tmp_path):
    if export.encoder_works("h264_nvenc"):
        pytest.skip("esta máquina sí tiene NVENC")
    with pytest.raises(RuntimeError, match="Ningún encoder"):
        export.export(make_test_clip(str(tmp_path / "in.mp4")),
                      str(tmp_path / "o.mp4"), prefer="gpu")


# --- extremo a extremo ------------------------------------------------------

@needs_ffmpeg
def test_full_pipeline_until_manual_review(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("RVSHORTS_COMFY_URLS", "http://127.0.0.1:9")
    src = make_test_clip(str(tmp_path / "largo.mp4"), seconds=6)
    clip = make_test_clip(str(tmp_path / "clip.mp4"), w=1080, h=1920, seconds=3)
    os_srv, llm_srv = FakeOpenShorts(clip, n_clips=2), FakeLLM()
    root = str(tmp_path / "proyectos")
    base = ["--openshorts", os_srv.url, "--llm-url", llm_srv.url + "/v1", "--raiz", root]
    try:
        assert main(base + ["diagnostico"]) == 0
        rc = main(base + ["pipeline", "Episodio 1", src, "--clips", "2",
                          "--codec", "h265", "--intervalo", "0.01"])
        assert rc == 0
        body = os_srv.process_bodies[0]
        assert body["acknowledged"] is True and body["target_clips"] == 2
        assert body["clip_min_seconds"] == 15 and body["clip_max_seconds"] == 60
        assert list(os_srv.uploads.values()) == [os.path.getsize(src)]   # subida completa

        p = Project(root, "Episodio 1")
        assert len(p.clip_files()) == 2
        meta = json.load(open(p.path("meta", "clip_00.json"), encoding="utf-8"))
        assert meta["openshorts"]["title"] == "Momento 0"
        assert meta["hooks"][0]["retention_score"] == 9
        assert os.path.exists(p.path("meta", "clip_01.md"))
        assert export.probe(p.path("exports", "clip_01_h265.mp4"))["video_codec"] == "hevc"
        assert all(e["status"] == "pendiente" for e in p.review()["clips"].values())
        assert not os.path.exists(p.path("publish_queue.json"))   # nada sin aprobar

        assert main(base + ["aprobar", "Episodio 1", "0", "--nota", "ok"]) == 0
        q = json.load(open(p.path("publish_queue.json"), encoding="utf-8"))
        assert q["items"][0]["exports"][0].endswith("clip_00_h265.mp4")
        out = capsys.readouterr().out
        assert "pendiente" in out and "gancho" in out
    finally:
        os_srv.close()
        llm_srv.close()


@needs_ffmpeg
def test_failed_openshorts_job_surfaces_logs(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("RVSHORTS_COMFY_URLS", "http://127.0.0.1:9")
    src = make_test_clip(str(tmp_path / "largo.mp4"))
    os_srv = FakeOpenShorts(src, fail=True)
    base = ["--openshorts", os_srv.url, "--raiz", str(tmp_path / "p")]
    try:
        assert main(base + ["nuevo", "x", src]) == 0
        assert main(base + ["procesar", "x", "--esperar", "--intervalo", "0.01"]) == 2
        assert "CUDA out of memory" in capsys.readouterr().err
    finally:
        os_srv.close()


def test_missing_video_is_a_clean_error(tmp_path, capsys):
    assert main(["--raiz", str(tmp_path), "nuevo", "x", str(tmp_path / "no.mp4")]) == 2
    assert "No existe el video" in capsys.readouterr().err


def test_analyze_script(tmp_path):
    srv = FakeLLM()
    try:
        f = tmp_path / "guion.txt"
        f.write_text("Capítulo 1. Ana abre la puerta y descubre que el estudio está vacío. "
                     "Nadie contesta. Entonces suena el teléfono.", encoding="utf-8")
        assert main(["--llm-url", srv.url + "/v1", "analizar-guion", str(f)]) == 0
        md = (tmp_path / "guion.analisis.md").read_text(encoding="utf-8")
        assert "Ganchos" in md and "Estructura narrativa" in md
    finally:
        srv.close()


# --- protección de VRAM frente a ComfyUI -------------------------------------

from fakes import FakeComfy  # noqa: E402
from rvshorts.gpu_guard import comfy_busy  # noqa: E402


def test_comfy_guard_detects_busy_and_ignores_offline():
    busy, idle = FakeComfy(running=1, pending=2), FakeComfy()
    try:
        out = comfy_busy(f"{busy.url},{idle.url},http://127.0.0.1:9")
        assert out == [f"{busy.url}: 1 en curso, 2 pendientes"]
    finally:
        busy.close(); idle.close()


@needs_ffmpeg
def test_procesar_refuses_while_comfy_renders(tmp_path, monkeypatch, capsys):
    src = make_test_clip(str(tmp_path / "largo.mp4"))
    comfy, os_srv = FakeComfy(running=1), FakeOpenShorts(src)
    monkeypatch.setenv("RVSHORTS_COMFY_URLS", comfy.url)
    base = ["--openshorts", os_srv.url, "--raiz", str(tmp_path / "p")]
    try:
        main(base + ["nuevo", "x", src])
        assert main(base + ["procesar", "x"]) == 3
        assert "renders activos" in capsys.readouterr().out
        assert os_srv.uploads == {}                       # no se subió nada
        assert main(base + ["procesar", "x", "--ignorar-cola"]) == 0
        assert len(os_srv.process_bodies) == 1
    finally:
        comfy.close(); os_srv.close()
