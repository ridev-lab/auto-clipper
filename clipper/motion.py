"""Intensité de mouvement (différence entre images) sur le proxy d'analyse :
utile pour le gameplay sans voix (poursuites, fusillades). Calcule aussi
le centre horizontal du mouvement, utilisé par le layout « track »."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .utils import Context, cached_step

HOP = 0.5
W, H = 160, 90


def analyze_motion(ctx: Context, analysis: Path, duration: float, fps: float = 10.0, pct_range=(50.0, 100.0)) -> dict:
    def compute() -> dict:
        import cv2

        lo, hi = pct_range
        n_bins = int(np.ceil(duration / HOP))
        per_bin: list[list[float]] = [[] for _ in range(n_bins + 1)]
        cx_bin: list[list[float]] = [[] for _ in range(n_bins + 1)]
        cap = cv2.VideoCapture(str(analysis))
        xs = np.arange(W, dtype=np.float32)[None, :]
        prev = None
        i = 0
        total = max(1, int(duration * fps))
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            g = cv2.cvtColor(cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            g = cv2.GaussianBlur(g, (3, 3), 0)
            if prev is not None:
                d = cv2.absdiff(g, prev)
                b = min(n_bins, int(i / fps / HOP))
                per_bin[b].append(float(d.mean()))
                mask = d > 22
                cnt = int(mask.sum())
                if cnt > 25:
                    cx_bin[b].append(float((xs * mask).sum() / cnt / W))
            prev = g
            i += 1
            if i % 300 == 0:
                ctx.progress(lo + (hi - lo) * min(1.0, i / total), "Analyse du mouvement")
        cap.release()
        # médiane par bin : une coupe (pic isolé) ne compte pas comme du mouvement
        raw = np.array([np.median(v) if v else 0.0 for v in per_bin[:n_bins]], dtype=np.float64)
        cx = [round(float(np.median(v)), 3) if v else None for v in cx_bin[:n_bins]]
        if raw.size and raw.max() > 0:
            p10, p95 = np.percentile(raw, 10), np.percentile(raw, 95)
            norm = np.clip((raw - p10) / max(1e-6, p95 - p10), 0, 1)
        else:
            norm = raw
        ctx.progress(hi, "Mouvement analysé")
        return {"hop": HOP, "motion": np.round(norm, 3).tolist(), "cx": cx}

    return cached_step(ctx, "motion", {"v": 1}, compute)
