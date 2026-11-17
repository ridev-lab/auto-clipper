"""Détection des changements de scène (PySceneDetect) sur le proxy d'analyse."""
from __future__ import annotations

from pathlib import Path

from .utils import Context, cached_step


def _seconds(tc) -> float:
    for attr in ("seconds", "get_seconds"):
        v = getattr(tc, attr, None)
        if v is not None:
            return float(v() if callable(v) else v)
    return float(tc)


def detect_scenes(ctx: Context, analysis: Path, duration: float, pct_range=(0.0, 50.0)) -> dict:
    def compute() -> dict:
        from scenedetect import ContentDetector, SceneManager, open_video

        lo, hi = pct_range
        video = open_video(str(analysis))
        sm = SceneManager()
        # proxy à 10 i/s : 5 images = 0,5 s minimum entre deux coupes
        sm.add_detector(ContentDetector(threshold=27.0, min_scene_len=5))
        t, step = 0.0, 120.0
        while t < duration:
            ctx.check_cancel()
            t = min(duration + 1, t + step)
            sm.detect_scenes(video=video, end_time=t)
            ctx.progress(lo + (hi - lo) * min(1.0, t / duration), "Détection des changements de scène")
        scenes = sm.get_scene_list()
        cuts = [round(_seconds(s[0]), 2) for s in scenes[1:]]
        ctx.log(f"{len(cuts)} changements de scène détectés")
        return {"cuts": cuts}

    return cached_step(ctx, "scenes", {"v": 1}, compute)
