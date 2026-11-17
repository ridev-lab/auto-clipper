"""Préparation unique par projet : audio WAV 16 kHz mono, proxy 480p pour
le navigateur, proxy d'analyse 320 px à 10 i/s et planche de vignettes."""
from __future__ import annotations

import math
from pathlib import Path

from .media import encoder_args, probe
from .utils import Context, cached_step, run_ffmpeg

ANALYSIS_FPS = 10
SPRITE_COLS, SPRITE_ROWS = 20, 10
THUMB_W, THUMB_H = 160, 90


def prepare(ctx: Context, src: Path) -> dict:
    info = probe(src)
    params = {"src": src.name, "size": info["size"], "v": 2}

    def compute() -> dict:
        dur = info["duration"]
        preview_fps = min(30.0, info["fps"])
        filt = (
            "[0:v]split=2[pv][an];"
            f"[pv]fps={preview_fps},scale=-2:480:flags=bilinear,format=yuv420p[pvo];"
            f"[an]fps={ANALYSIS_FPS},scale=320:-2:flags=area,format=yuv420p[ano]"
        )
        args = ["-i", str(src), "-filter_complex", filt]
        args += ["-map", "[pvo]"]
        if info["has_audio"]:
            args += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "96k", "-ac", "2"]
        args += encoder_args("proxy", preview_fps) + ["-movflags", "+faststart", str(ctx.path("preview.mp4"))]
        args += ["-map", "[ano]", "-an"] + encoder_args("proxy", ANALYSIS_FPS) + [str(ctx.path("analysis.mp4"))]
        if info["has_audio"]:
            args += ["-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(ctx.path("audio.wav"))]
        ctx.progress(0, "Extraction de l'audio et création des proxys")
        run_ffmpeg(args, ctx, duration=dur, pct_range=(0, 92), label="Extraction audio + proxys")

        # planche de vignettes (timeline)
        count = SPRITE_COLS * SPRITE_ROWS
        interval = max(1.0, math.ceil(dur / (count - 1) * 100) / 100)
        n = min(count, int(dur // interval) + 1)
        vf = (
            f"fps=1/{interval},scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=decrease,"
            f"pad={THUMB_W}:{THUMB_H}:(ow-iw)/2:(oh-ih)/2,tile={SPRITE_COLS}x{SPRITE_ROWS}"
        )
        run_ffmpeg(
            ["-i", str(ctx.path("analysis.mp4")), "-vf", vf, "-frames:v", "1", "-q:v", "5", str(ctx.path("thumbs.jpg"))],
            ctx, pct_range=(92, 100), label="Vignettes",
        )
        ctx.progress(100, "Préparation terminée")
        return {
            "probe": info,
            "preview": "preview.mp4",
            "analysis": "analysis.mp4",
            "analysis_fps": ANALYSIS_FPS,
            "audio": "audio.wav" if info["has_audio"] else None,
            "sprite": {
                "file": "thumbs.jpg",
                "interval": interval,
                "count": n,
                "cols": SPRITE_COLS,
                "w": THUMB_W,
                "h": THUMB_H,
            },
        }

    data = cached_step(ctx, "prepare", params, compute)
    # le cache peut survivre à la suppression manuelle d'un fichier
    if not ctx.path("preview.mp4").exists() or not ctx.path("analysis.mp4").exists():
        ctx.path("prepare.json").unlink(missing_ok=True)
        data = cached_step(ctx, "prepare", params, compute)
    return data
