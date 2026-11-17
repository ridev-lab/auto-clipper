"""Sélection des moments forts : combine le LLM (transcription), l'audio,
les changements de scène et le mouvement."""
from __future__ import annotations

import math
import re

import numpy as np

from . import llm
from .utils import ClipperError, Context, cached_step, fmt_time

HOP = 0.5
END_PUNCT = re.compile(r"[.!?…]+[\"»”)]*$")


# --------------------------------------------------------------------------
# Phrases
# --------------------------------------------------------------------------

def build_sentences(words: list[dict], max_words: int = 28, gap: float = 1.0) -> list[dict]:
    """Regroupe les mots en phrases (ponctuation forte, pause > gap, ou longueur)."""
    out: list[dict] = []
    start = 0
    for i, w in enumerate(words):
        last = i == len(words) - 1
        nxt_gap = 0.0 if last else words[i + 1]["s"] - w["e"]
        n = i - start + 1
        if last or END_PUNCT.search(w["t"]) or nxt_gap > gap or n >= max_words:
            seg = words[start : i + 1]
            out.append({
                "id": len(out),
                "s": seg[0]["s"],
                "e": seg[-1]["e"],
                "w0": start,
                "w1": i + 1,
                "text": " ".join(x["t"] for x in seg),
            })
            start = i + 1
    return out


# --------------------------------------------------------------------------
# Signaux
# --------------------------------------------------------------------------

def _slice(arr: np.ndarray, s: float, e: float) -> np.ndarray:
    a, b = int(s / HOP), max(int(s / HOP) + 1, int(math.ceil(e / HOP)))
    return arr[a:b]


def raw_signals(s: float, e: float, excite: np.ndarray, motion: np.ndarray, cuts: np.ndarray) -> dict:
    ex = _slice(excite, s, e)
    mo = _slice(motion, s, e)
    if ex.size:
        top = np.sort(ex)[-max(1, int(ex.size * 0.3)) :]
        audio = 0.6 * float(top.mean()) + 0.4 * float(ex.max())
    else:
        audio = 0.0
    motion_v = float(np.mean(np.sort(mo)[mo.size // 2 :])) if mo.size else 0.0
    n_cuts = int(((cuts >= s) & (cuts < e)).sum()) if cuts.size else 0
    scenes = n_cuts / max(1.0, e - s)
    return {"audio": audio, "motion": motion_v, "scenes": scenes}


def percentile_rank(value: float, ref: np.ndarray) -> float:
    if ref.size == 0:
        return 0.0
    less = float((ref < value - 1e-9).sum())
    equal = float((np.abs(ref - value) <= 1e-9).sum())
    return (less + 0.5 * equal) / ref.size


class SignalModel:
    """Normalise les signaux bruts par rapport à toute la vidéo (rang
    percentile parmi des fenêtres glissantes)."""

    def __init__(self, duration: float, excite, motion, cuts, ref_len: float):
        self.duration = duration
        self.excite = np.asarray(excite, dtype=np.float64)
        self.motion = np.asarray(motion, dtype=np.float64)
        self.cuts = np.asarray(cuts, dtype=np.float64)
        ref_len = min(ref_len, max(5.0, duration))
        starts = np.arange(0, max(0.01, duration - ref_len + 0.01), 2.0)
        refs = [raw_signals(float(t), float(t) + ref_len, self.excite, self.motion, self.cuts) for t in starts]
        self.ref = {k: np.array([r[k] for r in refs]) for k in ("audio", "motion", "scenes")}

    def scores(self, s: float, e: float) -> dict:
        raw = raw_signals(s, e, self.excite, self.motion, self.cuts)
        out = {}
        for k, v in raw.items():
            ref = self.ref[k]
            if ref.size == 0 or float(ref.max()) <= 1e-9:
                out[k] = 0.0
            else:
                out[k] = round(percentile_rank(v, ref), 3)
        return out

    def interest_curve(self, weights: dict) -> np.ndarray:
        n = int(math.ceil(self.duration / HOP))
        ex = np.resize(self.excite, n) if self.excite.size else np.zeros(n)
        mo = np.resize(self.motion, n) if self.motion.size else np.zeros(n)
        cut = np.zeros(n)
        for c in self.cuts:
            i = int(c / HOP)
            if 0 <= i < n:
                cut[i] += 1
        k = np.ones(int(6 / HOP)) / int(6 / HOP)
        cut_d = np.convolve(cut, k, mode="same")
        if cut_d.max() > 0:
            cut_d = cut_d / max(cut_d.max(), 1e-6)
        wa, wm, ws = weights.get("audio", 0.4), weights.get("motion", 0.35), weights.get("scenes", 0.25)
        curve = (wa * ex + wm * mo + ws * cut_d) / max(1e-6, wa + wm + ws)
        k2 = np.ones(int(4 / HOP)) / int(4 / HOP)
        return np.convolve(curve, k2, mode="same")


def signal_candidates(model: SignalModel, weights: dict, n: int, min_d: float, max_d: float) -> list[dict]:
    curve = model.interest_curve(weights)
    if curve.size == 0 or curve.max() <= 0:
        return []
    order = np.argsort(curve)[::-1]
    taken: list[tuple[float, float]] = []
    out: list[dict] = []
    for idx in order:
        if len(out) >= n:
            break
        peak = float(curve[idx])
        if peak <= 0:
            break
        t = idx * HOP
        if any(a - 2 <= t <= b + 2 for a, b in taken):
            continue
        # étend la fenêtre tant que l'intérêt reste élevé autour du pic
        lo = hi = idx
        thr = 0.55 * peak
        while lo > 0 and curve[lo - 1] >= thr and (idx - lo) * HOP < max_d * 0.6:
            lo -= 1
        while hi < curve.size - 1 and curve[hi + 1] >= thr and (hi - idx) * HOP < max_d * 0.6:
            hi += 1
        s = lo * HOP - 4.0  # mise en place avant l'action
        e = hi * HOP + 2.5
        s, e = fit_duration(s, e, min_d, max_d, model.duration, anchor=t)
        if any(not (e <= a or s >= b) for a, b in taken):
            continue
        taken.append((s, e))
        out.append({"start": s, "end": e, "source": "signal", "peak": round(peak, 3)})
    return out


def fit_duration(s: float, e: float, min_d: float, max_d: float, total: float, anchor: float | None = None) -> tuple[float, float]:
    """Force la durée dans [min_d, max_d] en restant dans la vidéo."""
    s, e = max(0.0, s), min(total, e)
    d = e - s
    if d > max_d:
        if anchor is not None:
            # on garde l'ancre (le pic) avec ~40 % de préparation avant
            s = max(0.0, min(anchor - max_d * 0.4, total - max_d))
        e = s + max_d
    elif d < min_d:
        missing = min_d - d
        s = max(0.0, s - missing * 0.4)
        e = min(total, s + min_d)
        s = max(0.0, e - min_d)
    return round(s, 3), round(min(e, total), 3)


# --------------------------------------------------------------------------
# Bornes sur les mots et phrases
# --------------------------------------------------------------------------

def adjust_bounds(
    s: float, e: float, words: list[dict], sentences: list[dict], margin: float,
    min_d: float, max_d: float, total: float, snap: float = 4.0,
) -> tuple[float, float]:
    """Évite de couper une phrase ou un mot et ajoute une marge de `margin` s."""
    if not words:
        return fit_duration(s, e, min_d, max_d, total)
    # début : au début de la phrase en cours si elle a commencé il y a peu
    for st in sentences:
        if st["s"] < s < st["e"]:
            if s - st["s"] <= snap:
                s = st["s"]
            elif st["e"] - s <= 3.0:
                s = st["e"] + 0.05
            break
    # fin : à la fin de la phrase en cours si elle se termine bientôt
    for st in sentences:
        if st["s"] < e < st["e"]:
            if st["e"] - e <= snap:
                e = st["e"]
            elif e - st["s"] <= 3.0:
                e = st["s"] - 0.05
            break
    # jamais au milieu d'un mot
    for w in words:
        if w["s"] < s < w["e"]:
            s = w["s"]
        if w["s"] < e < w["e"]:
            e = w["e"]
    # marges : sans empiéter sur le mot voisin
    prev_end = max((w["e"] for w in words if w["e"] <= s + 1e-3), default=None)
    first = next((w for w in words if w["s"] >= s - 1e-3), None)
    if first and first["s"] - s < margin:
        s = first["s"] - margin
        if prev_end is not None:
            s = max(s, prev_end + 0.02)
    last = None
    for w in words:
        if w["e"] <= e + 1e-3:
            last = w
    next_start = min((w["s"] for w in words if w["s"] >= e - 1e-3), default=None)
    if last and e - last["e"] < margin:
        e = last["e"] + margin
        if next_start is not None:
            e = min(e, next_start - 0.02)
    s, e = max(0.0, s), min(total, e)
    if e - s > max_d + 0.8:
        # trop long : on recule la fin jusqu'à une fin de mot
        limit = s + max_d
        ends = [w["e"] for w in words if s < w["e"] <= limit]
        e = (max(ends) + min(margin, 0.2)) if ends else limit
    if e - s < min_d - 0.8:
        s, e = fit_duration(s, e, min_d, max_d, total)
    return round(s, 3), round(e, 3)


# --------------------------------------------------------------------------
# LLM
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """Tu es un monteur expert en vidéos courtes virales (TikTok, YouTube Shorts, Instagram Reels), spécialisé dans le gaming et GTA.
On te donne un extrait de transcription découpé en phrases numérotées avec leur horodatage.
Ta mission : repérer les meilleurs moments à transformer en clips verticaux autonomes.

Un bon clip :
- commence par une accroche forte (la première phrase donne envie de rester) ;
- se comprend sans contexte et raconte une mini-histoire complète ;
- se termine sur une phrase complète (chute, punchline, réaction) ;
- contient de l'émotion : humour, surprise, rage, tension, action, moment épique, réaction forte, info croustillante.
À éviter : intro, « abonnez-vous », sponsor, passages calmes ou techniques, moments qui demandent du contexte.
Réponds uniquement avec du JSON valide."""

LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_id": {"type": "integer"},
                    "end_id": {"type": "integer"},
                    "hook": {"type": "string"},
                    "reason": {"type": "string"},
                    "score": {"type": "integer"},
                },
                "required": ["start_id", "end_id", "hook", "reason", "score"],
            },
        }
    },
    "required": ["moments"],
}


def _mmss(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:04.1f}"


def llm_windows(sentences: list[dict], window: float, overlap: float) -> list[list[dict]]:
    if not sentences:
        return []
    out, t0 = [], sentences[0]["s"]
    end = sentences[-1]["e"]
    while True:
        chunk = [s for s in sentences if s["s"] >= t0 and s["s"] < t0 + window]
        if chunk:
            out.append(chunk)
        if t0 + window >= end:
            break
        t0 += max(30.0, window - overlap)
    return out


def _fit_sentences(sents: list[dict], a: int, b: int, min_d: float, max_d: float) -> tuple[int, int]:
    """Ajuste une plage de phrases [a, b] pour respecter les durées."""
    def dur(i, j):
        return sents[j]["e"] - sents[i]["s"]

    while dur(a, b) < min_d:
        can_next = b + 1 < len(sents) and dur(a, b + 1) <= max_d
        can_prev = a - 1 >= 0 and dur(a - 1, b) <= max_d
        if can_next:
            b += 1
        elif can_prev:
            a -= 1
        else:
            break
    while dur(a, b) > max_d and b > a:
        b -= 1
    return a, b


def llm_candidates(ctx: Context, sentences: list[dict], opts: dict, source_title: str, pct_range=(0, 100)) -> list[dict]:
    c = ctx.cfg["ollama"]
    min_d, max_d, n = opts["min_duration"], opts["max_duration"], opts["clips"]
    params = {"model": c["model"], "min": min_d, "max": max_d, "n": n, "v": 2, "nsent": len(sentences)}

    def compute() -> dict:
        lo, hi = pct_range
        wins = llm_windows(sentences, c["window_minutes"] * 60, c["overlap_minutes"] * 60)
        total = max(1.0, sentences[-1]["e"] - sentences[0]["s"]) if sentences else 1.0
        found: list[dict] = []
        failures = 0
        for wi, win in enumerate(wins):
            ctx.check_cancel()
            ctx.progress(lo + (hi - lo) * wi / max(1, len(wins)), f"Analyse IA {wi + 1}/{len(wins)}")
            span = win[-1]["e"] - win[0]["s"]
            k = int(min(5, max(2, math.ceil(n * 1.6 * span / total))))
            lines = [f"[{s['id']}] {_mmss(s['s'])}–{_mmss(s['e'])} {s['text']}" for s in win]
            user = (
                f"Titre de la vidéo : {source_title or 'inconnu'}\n"
                f"Durée d'un clip : entre {min_d:.0f} et {max_d:.0f} secondes.\n"
                f"Propose au maximum {k} moments (moins s'il n'y a rien de bon), sans chevauchement.\n"
                "Pour chaque moment : start_id et end_id (numéros des phrases de début et de fin, inclus), "
                "hook (titre accrocheur en français, 3 à 8 mots, sans hashtag ni emoji), "
                "reason (pourquoi ce moment marche, une phrase en français), "
                "score (potentiel viral de 0 à 100, sois exigeant : 80+ = exceptionnel).\n\n"
                "Transcription :\n" + "\n".join(lines)
            )
            try:
                res = llm.chat_json(SYSTEM_PROMPT, user, LLM_SCHEMA)
            except ClipperError as e:
                failures += 1
                ctx.warn(f"Fenêtre IA {wi + 1} ignorée : {e}")
                if failures >= 3 and failures == wi + 1:
                    raise
                continue
            ids = {s["id"]: i for i, s in enumerate(sentences)}
            for m in res.get("moments", []) or []:
                try:
                    a, b = int(m["start_id"]), int(m["end_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                if a > b:
                    a, b = b, a
                if a not in ids or b not in ids:
                    continue
                ia, ib = _fit_sentences(sentences, ids[a], ids[b], min_d, max_d)
                s, e = sentences[ia]["s"], sentences[ib]["e"]
                if e - s < min_d * 0.6:
                    continue
                found.append({
                    "start": round(s, 3),
                    "end": round(e, 3),
                    "source": "llm",
                    "llm": max(0, min(100, int(m.get("score", 0)))) / 100,
                    "hook": str(m.get("hook", "")).strip().strip('"')[:90],
                    "reason": str(m.get("reason", "")).strip()[:300],
                })
        ctx.progress(hi, "Analyse IA terminée")
        # dédoublonnage (fenêtres qui se chevauchent)
        found.sort(key=lambda x: x["llm"], reverse=True)
        kept: list[dict] = []
        for f in found:
            if all(iou(f, k) < 0.4 for k in kept):
                kept.append(f)
        ctx.log(f"IA : {len(kept)} moments candidats")
        return {"candidates": kept}

    return cached_step(ctx, "llm_candidates", params, compute)["candidates"]


# --------------------------------------------------------------------------
# Combinaison et sélection
# --------------------------------------------------------------------------

def iou(a: dict, b: dict) -> float:
    inter = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    union = max(a["end"], b["end"]) - min(a["start"], b["start"])
    return inter / union if union > 0 else 0.0


def overlaps(a: dict, b: dict, gap: float = 0.0) -> bool:
    return not (a["end"] + gap <= b["start"] or b["end"] + gap <= a["start"])


def select_top(cands: list[dict], n: int, gap: float) -> list[dict]:
    chosen: list[dict] = []
    for c in sorted(cands, key=lambda x: x["score"], reverse=True):
        if len(chosen) >= n:
            break
        if all(not overlaps(c, k, gap) for k in chosen):
            chosen.append(c)
    return chosen


def _wsum(sig: dict, weights: dict) -> float:
    tot = sum(weights.values()) or 1.0
    return sum(weights[k] * sig.get(k, 0.0) for k in weights) / tot


def find_highlights(
    ctx: Context, transcript: dict, audio: dict, scenes: dict, motion: dict, duration: float,
    opts: dict, source_title: str = "", pct_range=(0, 100),
) -> dict:
    sc = ctx.cfg["scoring"]
    n, min_d, max_d = int(opts["clips"]), float(opts["min_duration"]), float(opts["max_duration"])
    if duration < min_d:
        min_d = max(3.0, duration * 0.8)
        max_d = max(min_d, duration)
    opts = {**opts, "min_duration": min_d, "max_duration": max_d}
    words = transcript.get("words", [])
    sentences = build_sentences(words)
    speech_ratio = float(transcript.get("speech_ratio") or 0)
    voice = speech_ratio >= float(ctx.cfg["transcription"]["min_speech_ratio"]) and len(sentences) >= 3

    model = SignalModel(duration, audio.get("excite", []), motion.get("motion", []), scenes.get("cuts", []), (min_d + max_d) / 2)
    lo, hi = pct_range
    llm_ok, llm_msg, llm_c = False, "", []
    if voice:
        ok, msg = llm.available(ctx)
        if ok:
            try:
                llm_c = llm_candidates(ctx, sentences, opts, source_title, (lo, lo + (hi - lo) * 0.9))
                llm_ok = True
            except ClipperError as e:
                llm_msg = str(e)
        else:
            llm_msg = msg
        if llm_msg:
            ctx.warn(f"Scoring sans IA : {llm_msg}")
    else:
        ctx.log(f"Peu ou pas de voix ({speech_ratio:.0%}) : profil gameplay (audio + scènes + mouvement)")

    w_voice = dict(sc["weights"])
    w_sig = dict(sc["weights_no_voice"]) if not (voice and llm_ok) else {k: v for k, v in w_voice.items() if k != "llm"}
    sig_c = signal_candidates(model, w_sig, n * 4, min_d, max_d)

    cands: list[dict] = []
    for c in llm_c:
        s, e = adjust_bounds(c["start"], c["end"], words, sentences, sc["boundary_margin"], min_d, max_d, duration)
        sig = model.scores(s, e)
        sig["llm"] = c["llm"]
        cands.append({**c, "start": s, "end": e, "signals": sig, "score": _wsum(sig, w_voice)})
    for c in sig_c:
        if any(iou(c, k) > 0.3 for k in cands):
            continue
        s, e = adjust_bounds(c["start"], c["end"], words, sentences, sc["boundary_margin"], min_d, max_d, duration)
        sig = model.scores(s, e)
        score = _wsum(sig, w_sig)
        if voice and llm_ok:
            score *= float(sc["signal_only_factor"])
        cands.append({**c, "start": s, "end": e, "signals": sig, "score": score, "hook": "", "reason": _signal_reason(sig)})

    for c in cands:
        c["score"] = int(round(max(0.0, min(1.0, c["score"])) * 100))
        c["signals"] = {k: round(float(v), 3) for k, v in c["signals"].items()}
    chosen = select_top(cands, n, float(sc["min_gap"]))
    chosen.sort(key=lambda x: x["score"], reverse=True)
    ctx.log(f"{len(chosen)} clips retenus sur {len(cands)} candidats")
    return {
        "voice": voice,
        "llm_used": llm_ok,
        "llm_message": llm_msg,
        "speech_ratio": speech_ratio,
        "sentences": len(sentences),
        "candidates": sorted(cands, key=lambda x: x["score"], reverse=True)[: n * 4],
        "selected": chosen,
    }


def _signal_reason(sig: dict) -> str:
    parts = []
    if sig.get("audio", 0) >= 0.75:
        parts.append("pic sonore (cris, explosions, réactions)")
    if sig.get("motion", 0) >= 0.75:
        parts.append("action intense à l'écran")
    if sig.get("scenes", 0) >= 0.75:
        parts.append("montage rapide")
    if not parts:
        parts.append("moment le plus animé de cette partie de la vidéo")
    return "Détecté par l'analyse du signal : " + ", ".join(parts) + "."


def describe(c: dict) -> str:
    return f"{fmt_time(c['start'])}–{fmt_time(c['end'])} ({c['end'] - c['start']:.0f}s) score {c['score']}"
