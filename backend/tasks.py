"""Orchestration des traitements, partagée par la file de jobs (serveur)
et la CLI : traitement complet d'un projet, rendu d'un clip, export ZIP."""
from __future__ import annotations

import time
import zipfile
from pathlib import Path

from clipper import llm
from clipper.audio_analysis import analyze_audio
from clipper.config import get_config, workspace_dir
from clipper.download import VIDEO_EXTS, download_youtube, import_file
from clipper.facecam import detect_facecam
from clipper.highlights import describe, find_highlights
from clipper.metadata import apply_keywords, generate as gen_metadata
from clipper.motion import analyze_motion
from clipper.prepare import prepare
from clipper.reframe import resolve_layout
from clipper.render import render_clip
from clipper.scenes import detect_scenes
from clipper.transcribe import transcribe
from clipper.utils import ClipperError, Context, cached_step, read_json

from . import db

STEP_WEIGHTS = {
    "ingest": 10, "prepare": 8, "transcribe": 25, "audio": 4,
    "scenes": 8, "scoring": 15, "reframe": 5, "render": 25,
}


def overall_progress(job_type: str, step: str, pct: float) -> float:
    if job_type != "process":
        return pct
    done = 0.0
    for name, w in STEP_WEIGHTS.items():
        if name == step:
            return round(done + w * pct / 100, 1)
        done += w
    return round(done, 1)


def project_options(p: dict) -> dict:
    d = get_config()["defaults"]
    opts = {
        "clips": d["clips"], "min_duration": d["min_duration"], "max_duration": d["max_duration"],
        "layout": d["layout"], "language": d["language"], "subtitle_preset": d["subtitle_preset"],
        "hook_title": d["hook_title"], "range_start": None, "range_end": None,
    }
    opts.update({k: v for k, v in (p.get("options") or {}).items() if v is not None})
    opts["clips"] = max(1, min(30, int(opts["clips"])))
    opts["min_duration"] = max(5.0, float(opts["min_duration"]))
    opts["max_duration"] = max(opts["min_duration"] + 1, float(opts["max_duration"]))
    return opts


def source_path(pid: str, p: dict | None = None) -> Path | None:
    p = p or db.get_project(pid)
    wd = workspace_dir(pid)
    if p and p.get("source_file") and (wd / p["source_file"]).exists():
        return wd / p["source_file"]
    return None


def clip_words_from_transcript(transcript: dict, start: float, end: float, previous: list[dict] | None = None) -> list[dict]:
    prev = {w["i"]: w for w in (previous or []) if "i" in w}
    out = []
    for i, w in enumerate(transcript.get("words", [])):
        if w["e"] <= start or w["s"] >= end:
            continue
        item = {"i": i, "s": w["s"], "e": w["e"], "t": w["t"], "kw": False}
        if i in prev:
            item["t"] = prev[i].get("t", item["t"])
            item["kw"] = bool(prev[i].get("kw"))
            if prev[i].get("del"):
                item["del"] = True
        out.append(item)
    return out


def excerpt_of(words: list[dict]) -> str:
    return " ".join(w["t"] for w in words if not w.get("del"))


# --------------------------------------------------------------------------
# Traitement complet
# --------------------------------------------------------------------------

def process_project(ctx: Context, pid: str) -> None:
    p = db.get_project(pid)
    if not p:
        raise ClipperError("Projet introuvable.")
    opts = project_options(p)
    db.update_project(pid, status="running", error=None)

    # 1. ingestion -----------------------------------------------------------
    ctx.set_step("ingest")
    src = source_path(pid, p)
    title = p.get("title") or ""
    if src is None:
        rs, re_ = opts.get("range_start"), opts.get("range_end")
        if p["source_type"] == "youtube":
            ctx.progress(1, "Connexion à YouTube")
            src, meta = download_youtube(ctx, p["source_url"], ctx.workdir, rs, re_)
            title = meta.get("title") or title
        else:
            up = opts.get("upload_path")
            if not up or not Path(up).exists():
                orig = [f for f in ctx.workdir.glob("original.*") if f.suffix.lower() in VIDEO_EXTS]
                if not orig:
                    raise ClipperError("Le fichier importé est introuvable : recrée le projet.")
                up = orig[0]
            src = import_file(ctx, Path(up), ctx.workdir, rs, re_, move=bool(opts.get("move_upload", True)))
            title = title or Path(p.get("source_name") or src.name).stem
        db.update_project(pid, source_file=src.name, title=title)
    ctx.progress(100, "Source prête")

    # 2. préparation ---------------------------------------------------------
    ctx.set_step("prepare")
    prep = prepare(ctx, src)
    info = prep["probe"]
    dur = info["duration"]
    db.update_project(pid, duration=dur, width=info["width"], height=info["height"], fps=info["fps"])
    if dur < 6:
        raise ClipperError("Vidéo trop courte (moins de 6 secondes).")
    audio = ctx.path(prep["audio"]) if prep.get("audio") else None

    # 3. transcription -------------------------------------------------------
    ctx.set_step("transcribe")
    tr = transcribe(ctx, audio, opts["language"], dur)
    db.update_project(pid, language=tr.get("language"))

    # 4-5. signaux -----------------------------------------------------------
    ctx.set_step("audio")
    au = analyze_audio(ctx, audio, dur)
    ctx.set_step("scenes")
    analysis = ctx.path(prep["analysis"])
    sc = detect_scenes(ctx, analysis, dur, (0, 45))
    mo = analyze_motion(ctx, analysis, dur, prep.get("analysis_fps", 10), (45, 100))

    # 6. scoring + métadonnées ----------------------------------------------
    ctx.set_step("scoring")
    existing = [c for c in db.list_clips(pid) if not c["manual"]]
    hint = get_config().get("metadata", {}).get("content_hint", "Gameplay GTA 6")
    if not existing:
        hl_params = {k: opts[k] for k in ("clips", "min_duration", "max_duration", "language")}
        hl_params["model"] = ctx.cfg["ollama"]["model"]
        hl_params["w"] = ctx.cfg["scoring"]

        def compute_hl() -> dict:
            hl = find_highlights(ctx, tr, au, sc, mo, dur, opts, title, (0, 70))
            sel = hl["selected"]
            for i, c in enumerate(sel):
                ctx.progress(70 + 28 * i / max(1, len(sel)), f"Titres et hashtags {i + 1}/{len(sel)}")
                words = clip_words_from_transcript(tr, c["start"], c["end"])
                if hl["llm_used"] or llm.available()[0]:
                    c["meta"] = gen_metadata(ctx, c, excerpt_of(words), title, hint)
                else:
                    from clipper.metadata import fallback

                    c["meta"] = fallback(c, title)
            return hl

        hl = cached_step(ctx, "highlights", hl_params, compute_hl)
        if hl.get("llm_message"):
            ctx.warn(f"IA : {hl['llm_message']}")
        llm.unload_model()
        if not hl["selected"]:
            raise ClipperError("Aucun moment exploitable trouvé dans cette vidéo.")
    else:
        hl = None
        ctx.log(f"{len(existing)} clips déjà créés : reprise du rendu")
    ctx.progress(100, "Moments sélectionnés")

    # 7. recadrage (facecam) + création des clips ---------------------------
    ctx.set_step("reframe")
    p = db.get_project(pid)
    facecam = p.get("facecam")
    if not (facecam and facecam.get("manual")):
        if opts["layout"] in ("auto", "facecam"):
            facecam = detect_facecam(ctx, src, info, pct_range=(0, 90))
            if not facecam.get("found"):
                ctx.log(f"Pas de facecam : {facecam.get('reason', 'non trouvée')} → layout blur")
        else:
            facecam = facecam or {"found": False}
        db.update_project(pid, facecam=facecam)
    if hl:
        layout = resolve_layout(opts["layout"], facecam)
        for rank, c in enumerate(hl["selected"]):
            meta = c.get("meta") or {}
            words = clip_words_from_transcript(tr, c["start"], c["end"])
            apply_keywords(words, meta.get("keywords", []))
            db.create_clip(
                pid, rank=rank, manual=0, start=c["start"], end=c["end"], score=c["score"], signals=c["signals"],
                hook=meta.get("hook") or c.get("hook") or "", reason=c.get("reason") or "", layout=layout,
                preset=opts["subtitle_preset"], show_hook=1 if opts["hook_title"] else 0, words=words,
                title=meta.get("title", ""), description=meta.get("description", ""),
                hashtags=meta.get("hashtags", []), render_status="pending", dirty=1,
            )
            ctx.log(f"Clip {rank + 1} : {describe(c)} — {meta.get('hook') or c.get('hook') or ''}")
    ctx.progress(100)

    # 8. rendu ---------------------------------------------------------------
    ctx.set_step("render")
    todo = [c for c in db.list_clips(pid) if not c.get("file") or c.get("dirty")]
    for i, c in enumerate(todo):
        lo, hi = 100 * i / len(todo), 100 * (i + 1) / len(todo)
        ctx.progress(lo, f"Clip {i + 1}/{len(todo)}")
        _render_one(ctx, pid, c, info, facecam, (lo, hi))
    db.update_project(pid, status="done", error=None)
    ctx.progress(100, "Terminé")


def _render_one(ctx: Context, pid: str, clip: dict, info: dict, facecam: dict | None, pct_range=(0, 100)) -> None:
    src = source_path(pid)
    if src is None:
        raise ClipperError("Fichier source introuvable : le projet doit être relancé.")
    db.update_clip(clip["id"], render_status="rendering")
    try:
        res = render_clip(ctx, src, info, clip, facecam, pct_range)
    except Exception:
        db.update_clip(clip["id"], render_status="error")
        raise
    db.update_clip(
        clip["id"], file=res["file"], thumb=res["thumb"], render_status="done",
        rendered_at=time.time(), dirty=0, layout=res["layout"],
    )


def render_clip_job(ctx: Context, pid: str, cid: str) -> None:
    ctx.set_step("render")
    p = db.get_project(pid)
    clip = db.get_clip(cid)
    if not p or not clip:
        raise ClipperError("Clip introuvable.")
    prep = read_json(ctx.path("prepare.json"))
    if not prep:
        raise ClipperError("Le projet n'est pas encore préparé.")
    if not clip.get("title"):
        ctx.progress(2, "Titre et hashtags")
        tr = read_json(ctx.path("transcript.json")) or {}
        hint = get_config().get("metadata", {}).get("content_hint", "Gameplay GTA 6")
        meta = gen_metadata(ctx, clip, excerpt_of(clip.get("words") or []), p.get("title") or "", hint)
        llm.unload_model()
        words = clip.get("words") or []
        apply_keywords(words, meta.get("keywords", []))
        upd = {"title": meta["title"], "description": meta["description"], "hashtags": meta["hashtags"], "words": words}
        if not clip.get("hook"):
            upd["hook"] = meta["hook"]
        db.update_clip(cid, **upd)
        clip = db.get_clip(cid)
    _render_one(ctx, pid, clip, prep["probe"], p.get("facecam"), (5, 100))
    ctx.progress(100, "Clip rendu")


def export_zip(pid: str) -> Path:
    from clipper.config import output_dir

    p = db.get_project(pid)
    clips = [c for c in db.list_clips(pid) if c.get("file")]
    out = output_dir(pid)
    if not clips:
        raise ClipperError("Aucun clip rendu à exporter.")
    zpath = out / f"clips_{pid}.zip"
    lines = [f"Projet : {p.get('title') or pid}", ""]
    with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_STORED) as z:
        for i, c in enumerate(clips, 1):
            f = out / c["file"]
            if not f.exists():
                continue
            z.write(f, f.name)
            lines += [
                f"=== {i}. {f.name} (score {c.get('score')}) ===",
                f"Titre : {c.get('title') or ''}",
                f"Description : {c.get('description') or ''}",
                "Hashtags : " + " ".join(c.get("hashtags") or []),
                "",
            ]
        z.writestr("titres_descriptions_hashtags.txt", "\n".join(lines).encode("utf-8-sig"))
    return zpath


def run_job(ctx: Context, job: dict) -> None:
    if job["type"] == "process":
        process_project(ctx, job["project_id"])
    elif job["type"] == "render":
        render_clip_job(ctx, job["project_id"], job["clip_id"])
    else:
        raise ClipperError(f"Type de job inconnu : {job['type']}")
