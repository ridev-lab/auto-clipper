"""Processus séparé de transcription (faster-whisper).

Lancé par clipper.transcribe dans son propre processus : la VRAM est
entièrement libérée à la fin et un plantage natif (CUDA) ne fait pas
tomber le serveur. Communique par lignes JSON sur stdout."""
from __future__ import annotations

import argparse
import gc
import json
import re
import sys
import time

from clipper.cudasetup import register_cuda_dlls

# Phrases fantômes classiques de Whisper sur du silence / de la musique
HALLUCINATIONS = [
    r"sous-titr\w* (réalisés |par |fait )?.*amara",
    r"amara\.org",
    r"merci d'avoir regardé",
    r"abonnez-vous",
    r"thanks for watching",
    r"thank you for watching",
    r"subtitles by",
    r"sous-titrage st'? ?501",
    r"^\W*(musique|music|applause|applaudissements)\W*$",
]
_HALLU = re.compile("|".join(HALLUCINATIONS), re.I)


_PUNCT_ONLY = re.compile(r"^[\W_]+$", re.U)
_JOIN_PREV = re.compile(r"^['’\-]")          # 'est, -mère, -y
_JOIN_NEXT = re.compile(r"['’]$")            # j', l', c'


def merge_tokens(words: list[dict]) -> list[dict]:
    """Recolle les morceaux que Whisper sépare en français :
    c + 'est → c'est, grand + -mère → grand-mère, quoi + ? → quoi ?"""
    out: list[dict] = []
    for w in words:
        t = w["t"]
        if out:
            prev = out[-1]
            close = w["s"] - prev["e"] < 0.35
            if close and (_JOIN_PREV.match(t) or _JOIN_NEXT.search(prev["t"])):
                prev["t"] += t
                prev["e"] = w["e"]
                prev["p"] = min(prev.get("p", 1), w.get("p", 1))
                continue
            if _PUNCT_ONLY.match(t):
                sep = " " if t[:1] in "?!:;»-–—" else ""
                prev["t"] += sep + t
                prev["e"] = max(prev["e"], w["e"])
                continue
        out.append(dict(w))
    return out


def emit(**kw):
    sys.stdout.write(json.dumps(kw, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def is_oom(e: Exception) -> bool:
    s = str(e).lower()
    return "out of memory" in s or "cudaerrormemoryallocation" in s or "cublas_status_alloc_failed" in s


def run(args) -> dict:
    register_cuda_dlls()
    from faster_whisper import BatchedInferencePipeline, WhisperModel

    attempts = [(args.device, args.compute)]
    if args.device == "cuda":
        attempts += [("cuda", "int8_float16"), ("cpu", "int8")]
    seen, plan = set(), []
    for a in attempts:
        if a not in seen:
            seen.add(a)
            plan.append(a)

    # WAV 16 kHz mono déjà extrait par ffmpeg : lu directement (évite PyAV)
    import numpy as np
    import soundfile as sf

    audio, sr = sf.read(args.audio, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        import soxr

        audio = soxr.resample(audio, sr, 16000).astype(np.float32)

    last_err: Exception | None = None
    for device, compute in plan:
        model = None
        try:
            emit(type="log", msg=f"Chargement de Whisper {args.model} ({device}, {compute})…")
            t0 = time.time()
            model = WhisperModel(args.model, device=device, compute_type=compute, download_root=args.models_dir)
            emit(type="log", msg=f"Modèle chargé en {time.time() - t0:.1f} s")
            pipe = BatchedInferencePipeline(model=model)
            segments, info = pipe.transcribe(
                audio,
                language=None if args.lang in ("", "auto") else args.lang,
                batch_size=args.batch if device == "cuda" else 4,
                word_timestamps=True,
                vad_filter=True,
                beam_size=5,
                condition_on_previous_text=False,
                language_detection_segments=3,
            )
            emit(type="log", msg=f"Langue détectée : {info.language} ({info.language_probability:.0%})")
            words, segs = [], []
            dur = max(0.1, float(info.duration))
            for seg in segments:
                text = (seg.text or "").strip()
                if not text:
                    continue
                m = _HALLU.search(text)
                # segment qui n'est QUE la phrase fantôme (pas une vraie phrase qui la contient)
                if m and len(text) <= len(m.group(0)) + 20:
                    continue
                if seg.no_speech_prob > 0.75 and seg.avg_logprob < -0.9:
                    continue
                w0 = len(words)
                seg_words = []
                for w in seg.words or []:
                    t = (w.word or "").strip()
                    if not t:
                        continue
                    seg_words.append({"s": round(w.start, 3), "e": round(max(w.end, w.start + 0.05), 3), "t": t, "p": round(w.probability, 3)})
                words.extend(merge_tokens(seg_words))
                if len(words) > w0:
                    segs.append({"s": round(seg.start, 3), "e": round(seg.end, 3), "text": text, "w0": w0, "w1": len(words)})
                emit(type="progress", pct=min(99.0, seg.end / dur * 100))
            speech = float(getattr(info, "duration_after_vad", 0) or 0)
            words.sort(key=lambda w: w["s"])
            return {
                "language": info.language,
                "language_probability": round(float(info.language_probability), 3),
                "duration": round(dur, 3),
                "speech_duration": round(speech, 3),
                "speech_ratio": round(min(1.0, speech / dur), 4),
                "device": device,
                "compute_type": compute,
                "model": args.model,
                "segments": segs,
                "words": words,
            }
        except Exception as e:  # essai suivant (mémoire GPU, DLL manquante…)
            last_err = e
            reason = "mémoire GPU insuffisante" if is_oom(e) else str(e).splitlines()[0][:200]
            emit(type="log", level="warning", msg=f"Échec avec {device}/{compute} : {reason}")
        finally:
            del model
            gc.collect()
    raise RuntimeError(f"Transcription impossible : {last_err}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--audio", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--model", default="large-v3-turbo")
    p.add_argument("--lang", default="auto")
    p.add_argument("--device", default="cuda")
    p.add_argument("--compute", default="float16")
    p.add_argument("--batch", type=int, default=16)
    p.add_argument("--models-dir", default=None)
    args = p.parse_args()
    try:
        result = run(args)
    except Exception as e:
        emit(type="error", msg=str(e))
        sys.exit(2)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    emit(type="done", words=len(result["words"]), device=result["device"])


if __name__ == "__main__":
    main()
