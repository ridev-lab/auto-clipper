"""Transcription : lance clipper.transcribe_worker dans un processus séparé."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .config import ROOT, models_dir
from .utils import ClipperError, Context, cached_step, read_json, run_process

EMPTY = {
    "language": None,
    "language_probability": 0,
    "duration": 0,
    "speech_duration": 0,
    "speech_ratio": 0.0,
    "device": None,
    "segments": [],
    "words": [],
}


def transcribe(ctx: Context, audio: Path | None, lang: str, duration: float) -> dict:
    tcfg = ctx.cfg["transcription"]
    if audio is None or not Path(audio).exists():
        ctx.warn("Pas de piste audio : transcription ignorée")
        return {**EMPTY, "duration": duration}
    params = {"model": tcfg["model"], "lang": lang or "auto", "v": 1}

    def compute() -> dict:
        from . import llm

        llm.unload_model(ctx)  # libère la VRAM occupée par Ollama
        out = ctx.path("transcript.raw.json")
        out.unlink(missing_ok=True)
        cmd = [
            sys.executable, "-m", "clipper.transcribe_worker",
            "--audio", str(audio), "--out", str(out),
            "--model", tcfg["model"], "--lang", lang or "auto",
            "--device", tcfg["device"], "--compute", tcfg["compute_type"],
            "--batch", str(tcfg["batch_size"]), "--models-dir", str(models_dir()),
        ]
        errors: list[str] = []

        def on_line(line: str):
            try:
                msg = json.loads(line)
            except ValueError:
                return
            kind = msg.get("type")
            if kind == "progress":
                ctx.progress(msg.get("pct", 0), "Transcription en cours")
            elif kind == "log":
                ctx.log(msg.get("msg", ""), msg.get("level", "info"))
            elif kind == "error":
                errors.append(msg.get("msg", ""))

        ctx.progress(0, "Chargement du modèle Whisper")
        try:
            run_process(cmd, ctx, on_line=on_line, cwd=ROOT)
        except ClipperError as e:
            detail = errors[-1] if errors else str(e)
            raise ClipperError(f"La transcription a échoué : {detail[:400]}") from None
        data = read_json(out)
        if not data:
            raise ClipperError("La transcription n'a produit aucun résultat.")
        out.unlink(missing_ok=True)
        ctx.log(
            f"Transcription : {len(data['words'])} mots, langue {data['language']}, "
            f"parole {data['speech_ratio']:.0%} ({data['device']}/{data['compute_type']})"
        )
        ctx.progress(100)
        return data

    return cached_step(ctx, "transcript", params, compute)
