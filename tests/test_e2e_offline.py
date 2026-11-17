"""Test de bout en bout hors ligne : vidéo générée → clips rendus.

Lent (≈1 min) : utilise le GPU, Ollama s'il est lancé (sinon scoring sans IA).
  python -m pytest tests/test_e2e_offline.py -v
"""
import shutil
from pathlib import Path

import pytest

from backend import db, tasks
from clipper.config import find_tool, output_dir, workspace_dir
from clipper.media import probe
from clipper.utils import Context

from make_test_video import make_test_video

pytestmark = pytest.mark.skipif(not find_tool("ffmpeg"), reason="ffmpeg requis")


def test_full_pipeline_offline(tmp_path):
    video = make_test_video(tmp_path / "test_video.mp4", duration=70)
    info = probe(video)
    assert info["has_audio"] and info["duration"] == pytest.approx(70, abs=1)

    opts = {
        "clips": 2, "min_duration": 10, "max_duration": 25, "layout": "auto", "language": "fr",
        "subtitle_preset": "classique", "hook_title": True, "upload_path": str(video), "move_upload": False,
    }
    p = db.create_project(source_type="file", source_name=video.name, title="Test hors ligne", options=opts)
    pid = p["id"]
    steps = []
    try:
        ctx = Context(pid, on_progress=lambda step, pct, msg: steps.append(step))
        tasks.process_project(ctx, pid)
        p = db.get_project(pid)
        assert p["status"] == "done"
        assert {"ingest", "prepare", "transcribe", "audio", "scenes", "scoring", "reframe", "render"} <= set(steps)
        tr = (workspace_dir(pid) / "transcript.json").read_text(encoding="utf-8")
        assert "police" in tr.lower()  # la voix de synthèse a bien été transcrite
        clips = db.list_clips(pid)
        assert 1 <= len(clips) <= 2
        for c in clips:
            f = output_dir(pid) / c["file"]
            out = probe(f)
            assert (out["width"], out["height"]) == (1080, 1920)
            assert out["has_audio"]
            assert 9.0 <= out["duration"] <= 26.5
            assert c["title"] and c["hashtags"]
            assert (output_dir(pid) / c["thumb"]).exists()
        # re-rendu d'un clip modifié (layout + preset)
        c = clips[0]
        db.update_clip(c["id"], layout="track", preset="gaming_neon", dirty=1)
        tasks.render_clip_job(Context(pid), pid, c["id"])
        c2 = db.get_clip(c["id"])
        assert c2["dirty"] == 0 and c2["layout"] == "track"
        assert probe(output_dir(pid) / c2["file"])["height"] == 1920
        # export ZIP
        z = tasks.export_zip(pid)
        assert z.exists() and z.stat().st_size > 10_000
    finally:
        db.delete_project(pid)
        shutil.rmtree(workspace_dir(pid), ignore_errors=True)
        shutil.rmtree(output_dir(pid), ignore_errors=True)
