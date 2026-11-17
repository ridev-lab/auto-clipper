"""Analyse audio (librosa) : courbe d'« excitation » par pas de 0,5 s,
combinant le niveau sonore (RMS) et les attaques (flux spectral),
normalisés par rapport à la vidéo elle-même."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .utils import Context, cached_step

HOP = 0.5
SR = 16000


def robust_norm(x: np.ndarray, valid: np.ndarray | None = None, span: float = 4.0) -> np.ndarray:
    """0 = niveau habituel de la vidéo, 1 = pic très au-dessus (≈ span écarts)."""
    ref = x[valid] if valid is not None and valid.any() else x
    if ref.size == 0:
        return np.zeros_like(x)
    med = float(np.median(ref))
    mad = float(np.median(np.abs(ref - med))) * 1.4826
    if mad < 1e-6:
        mad = float(np.std(ref)) or 1.0
    return np.clip((x - med) / (mad * span), 0, 1)


def analyze_audio(ctx: Context, audio: Path | None, duration: float) -> dict:
    n_bins = int(np.ceil(duration / HOP))

    if audio is None or not Path(audio).exists():
        return {"hop": HOP, "db": [-90.0] * n_bins, "excite": [0.0] * n_bins}

    def compute() -> dict:
        import librosa
        import soundfile as sf

        block = int(SR * HOP)
        chunk = block * 120  # 60 s
        rms: list[float] = []
        onset = np.zeros(n_bins + 1, dtype=np.float32)
        with sf.SoundFile(str(audio)) as f:
            total = max(1, f.frames)
            pos = 0
            while True:
                ctx.check_cancel()
                data = f.read(chunk, dtype="float32", always_2d=False)
                if data.size == 0:
                    break
                if data.ndim > 1:
                    data = data.mean(axis=1)
                n = int(np.ceil(data.size / block))
                padded = np.zeros(n * block, dtype=np.float32)
                padded[: data.size] = data
                rms.extend(np.sqrt((padded.reshape(n, block) ** 2).mean(axis=1) + 1e-12).tolist())
                if data.size >= 2048:
                    env = librosa.onset.onset_strength(y=data, sr=SR, hop_length=512)
                    t = pos / SR + np.arange(env.size) * 512 / SR
                    idx = np.minimum((t / HOP).astype(int), n_bins)
                    np.maximum.at(onset, idx, env.astype(np.float32))
                pos += data.size
                ctx.progress(pos / total * 100, "Analyse des pics sonores")
        rms_a = np.array(rms[:n_bins] + [1e-6] * max(0, n_bins - len(rms)), dtype=np.float64)
        db = 20 * np.log10(np.maximum(rms_a, 1e-6))
        valid = db > -55  # ignore les silences pour la référence
        loud = robust_norm(db, valid, span=3.0)
        on = robust_norm(np.log1p(onset[:n_bins].astype(np.float64)), valid, span=4.0)
        excite = np.clip(0.65 * loud + 0.35 * on, 0, 1)
        excite[~valid] = 0
        return {
            "hop": HOP,
            "db": np.round(db, 1).tolist(),
            "excite": np.round(excite, 3).tolist(),
        }

    return cached_step(ctx, "audio", {"v": 1, "hop": HOP}, compute)
