"""API FastAPI + service des fichiers de l'interface."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from clipper import llm
from clipper.config import FONTS_DIR, FRONTEND_DIR, get_config, output_dir, workspace_dir
from clipper.download import VIDEO_EXTS, is_youtube_url, youtube_info
from clipper.media import probe
from clipper.reframe import LAYOUTS, compute_track, layout_geometry
from clipper.subtitles import caption_payload, clip_words, get_preset, presets_public
from clipper.utils import STEPS, ClipperError, Context, read_json, write_json

from . import db, tasks
from .jobs import manager
from .security import LocalOnlyMiddleware

SAFE_NAME = re.compile(r"^[\w.\-]+$")
UPLOADS = workspace_dir() / "_uploads"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.conn()
    UPLOADS.mkdir(parents=True, exist_ok=True)
    manager.start()
    yield


app = FastAPI(title="Auto Clipper", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(LocalOnlyMiddleware)


@app.exception_handler(ClipperError)
async def clipper_error(_: Request, exc: ClipperError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


def _err(status: int, msg: str):
    raise HTTPException(status_code=status, detail=msg)


# --------------------------------------------------------------------------
# Interface et fichiers
# --------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(FRONTEND_DIR / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
app.mount("/fonts", StaticFiles(directory=FONTS_DIR), name="fonts")

MEDIA_FILES = {"preview.mp4", "thumbs.jpg", "frame_ref.jpg"}


@app.get("/media/{pid}/{name}")
def media(pid: str, name: str):
    if not SAFE_NAME.match(pid) or name not in MEDIA_FILES:
        _err(404, "Fichier introuvable")
    f = workspace_dir(pid) / name
    if not f.exists():
        _err(404, "Fichier introuvable")
    return FileResponse(f)


@app.get("/output/{pid}/{name}")
def output_file(pid: str, name: str):
    if not SAFE_NAME.match(pid) or not SAFE_NAME.match(name):
        _err(404, "Fichier introuvable")
    f = output_dir(pid) / name
    if not f.exists():
        _err(404, "Fichier introuvable")
    return FileResponse(f)


# --------------------------------------------------------------------------
# État / configuration
# --------------------------------------------------------------------------

_status_cache: dict = {"t": 0.0, "data": None}


@app.get("/api/status")
def status(refresh: bool = False):
    from clipper.doctor import gpu_info, run_checks

    now = time.time()
    if refresh or not _status_cache["data"] or now - _status_cache["t"] > 30:
        _status_cache["data"] = run_checks(quick=True)
        _status_cache["t"] = now
    queued = [j for j in db.list_jobs(active_only=True)]
    return {"checks": _status_cache["data"], "gpu": gpu_info(), "queue": len(queued)}


@app.get("/api/config")
def config():
    cfg = get_config()
    return {
        "defaults": cfg["defaults"],
        "presets": presets_public(),
        "layouts": list(LAYOUTS),
        "steps": [{"id": k, "label": v} for k, v in STEPS],
        "step_weights": tasks.STEP_WEIGHTS,
        "hook": {k: cfg["hook"][k] for k in ("font_file", "font_size", "color", "box_color", "duration")},
        "output": {"width": cfg["reframe"]["output_width"], "height": cfg["reframe"]["output_height"]},
    }


@app.get("/api/youtube/info")
async def yt_info(url: str):
    if not is_youtube_url(url):
        _err(400, "Ce n'est pas un lien YouTube valide.")
    return await run_in_threadpool(youtube_info, url)


# --------------------------------------------------------------------------
# Upload en morceaux (reprenable)
# --------------------------------------------------------------------------

def _upload_meta(uid: str) -> dict:
    if not SAFE_NAME.match(uid):
        _err(404, "Upload introuvable")
    meta = read_json(UPLOADS / f"{uid}.json")
    if not meta:
        _err(404, "Upload introuvable ou expiré")
    part = UPLOADS / f"{uid}.part"
    meta["received"] = part.stat().st_size if part.exists() else 0
    return meta


@app.post("/api/uploads")
async def upload_init(request: Request):
    body = await request.json()
    name = Path(str(body.get("filename") or "video.mp4")).name
    size = int(body.get("size") or 0)
    ext = Path(name).suffix.lower()
    if ext not in VIDEO_EXTS:
        _err(400, f"Format non supporté ({ext or 'inconnu'}). Formats acceptés : MP4, MOV, MKV, WEBM.")
    if size <= 0:
        _err(400, "Fichier vide.")
    free = shutil.disk_usage(UPLOADS).free
    if size * 2.2 > free:
        _err(400, f"Espace disque insuffisant ({free / 1e9:.0f} Go libres, il en faut environ {size * 2.2 / 1e9:.0f}).")
    uid = db.new_id(16)
    write_json(UPLOADS / f"{uid}.json", {"id": uid, "filename": name, "size": size, "created": time.time()})
    (UPLOADS / f"{uid}.part").touch()
    return {"id": uid, "received": 0, "size": size}


@app.get("/api/uploads/{uid}")
def upload_status(uid: str):
    return _upload_meta(uid)


@app.put("/api/uploads/{uid}")
async def upload_chunk(uid: str, request: Request, offset: int = 0):
    meta = _upload_meta(uid)
    part = UPLOADS / f"{uid}.part"
    received = meta["received"]
    if offset > received:
        return JSONResponse({"detail": "Décalage invalide", "received": received}, status_code=409)
    data = await request.body()
    if offset + len(data) > meta["size"]:
        _err(400, "Le morceau dépasse la taille annoncée.")

    def write():
        with open(part, "r+b") as f:
            f.seek(offset)
            f.write(data)
            f.truncate(offset + len(data))

    await run_in_threadpool(write)
    return {"received": offset + len(data), "size": meta["size"]}


@app.delete("/api/uploads/{uid}")
def upload_delete(uid: str):
    if SAFE_NAME.match(uid):
        for ext in (".part", ".json"):
            (UPLOADS / f"{uid}{ext}").unlink(missing_ok=True)
    return {"ok": True}


# --------------------------------------------------------------------------
# Projets
# --------------------------------------------------------------------------

def _clean_options(o: dict) -> dict:
    d = get_config()["defaults"]
    out = {}
    try:
        out["clips"] = max(1, min(30, int(o.get("clips", d["clips"]))))
        out["min_duration"] = max(5.0, min(170.0, float(o.get("min_duration", d["min_duration"]))))
        out["max_duration"] = max(out["min_duration"] + 1, min(180.0, float(o.get("max_duration", d["max_duration"]))))
    except (TypeError, ValueError):
        _err(400, "Options invalides.")
    out["layout"] = o.get("layout") if o.get("layout") in LAYOUTS else d["layout"]
    out["language"] = o.get("language") if o.get("language") in ("auto", "fr", "en") else d["language"]
    out["subtitle_preset"] = get_preset(o.get("subtitle_preset"))[0]
    out["hook_title"] = bool(o.get("hook_title", d["hook_title"]))
    for k in ("range_start", "range_end"):
        v = o.get(k)
        out[k] = float(v) if v not in (None, "") and float(v) >= 0 else None
    if out["range_start"] is not None and out["range_end"] is not None and out["range_end"] <= out["range_start"] + 10:
        _err(400, "La plage à analyser doit durer au moins 10 secondes.")
    return out


@app.post("/api/projects")
async def create_project(request: Request):
    body = await request.json()
    stype = body.get("source_type")
    opts = _clean_options(body.get("options") or {})
    if stype == "youtube":
        url = str(body.get("url") or "").strip()
        if not is_youtube_url(url):
            _err(400, "Ce n'est pas un lien YouTube valide.")
        p = db.create_project(source_type="youtube", source_url=url, source_name=url, title=str(body.get("title") or "")[:200], options=opts)
    elif stype == "upload":
        meta = _upload_meta(str(body.get("upload_id") or ""))
        if meta["received"] != meta["size"]:
            _err(400, "L'upload n'est pas terminé.")
        ext = Path(meta["filename"]).suffix.lower()
        final = UPLOADS / f"{meta['id']}{ext}"
        (UPLOADS / f"{meta['id']}.part").replace(final)
        try:
            await run_in_threadpool(probe, final)
        except ClipperError as e:
            final.unlink(missing_ok=True)
            (UPLOADS / f"{meta['id']}.json").unlink(missing_ok=True)
            _err(400, str(e))
        (UPLOADS / f"{meta['id']}.json").unlink(missing_ok=True)
        opts["upload_path"] = str(final)
        p = db.create_project(source_type="upload", source_name=meta["filename"], title=Path(meta["filename"]).stem, options=opts)
    else:
        _err(400, "Source inconnue.")
    job = manager.submit(p["id"], "process")
    return {"project": p, "job": job}


def _project_view(p: dict, with_clips: bool = True) -> dict:
    pid = p["id"]
    out = {k: v for k, v in p.items() if k != "options"}
    opts = dict(p.get("options") or {})
    opts.pop("upload_path", None)
    out["options"] = opts
    wd = workspace_dir(pid)
    out["preview_url"] = f"/media/{pid}/preview.mp4" if (wd / "preview.mp4").exists() else None
    out["frame_ref_url"] = f"/media/{pid}/frame_ref.jpg" if (wd / "frame_ref.jpg").exists() else None
    if with_clips:
        out["clips"] = [_clip_summary(c) for c in db.list_clips(pid)]
        out["jobs"] = db.list_jobs(pid)[-20:]
    return out


def _clip_summary(c: dict) -> dict:
    out = {k: v for k, v in c.items() if k != "words"}
    out["word_count"] = len(c.get("words") or [])
    v = int(c.get("rendered_at") or 0)
    out["file_url"] = f"/output/{c['project_id']}/{c['file']}?v={v}" if c.get("file") else None
    out["thumb_url"] = f"/output/{c['project_id']}/{c['thumb']}?v={v}" if c.get("thumb") else None
    return out


def _disk_usage(pid: str) -> int:
    total = 0
    for base in (workspace_dir(pid), output_dir(pid)):
        if base.exists():
            total += sum(f.stat().st_size for f in base.rglob("*") if f.is_file())
    return total


@app.get("/api/projects")
def list_projects():
    out = []
    for p in db.list_projects():
        v = _project_view(p, with_clips=False)
        v["clip_count"] = p.get("clip_count", 0)
        v["disk"] = _disk_usage(p["id"])
        v["thumb_url"] = None
        clips = db.list_clips(p["id"])
        if clips and clips[0].get("thumb"):
            v["thumb_url"] = f"/output/{p['id']}/{clips[0]['thumb']}"
        out.append(v)
    return out


def _get_project(pid: str) -> dict:
    p = db.get_project(pid) if SAFE_NAME.match(pid) else None
    if not p:
        _err(404, "Projet introuvable")
    return p


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return _project_view(_get_project(pid))


def _rmtree(path: Path) -> None:
    for _ in range(10):
        if not path.exists():
            return
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        time.sleep(0.5)


@app.delete("/api/projects/{pid}")
async def delete_project(pid: str):
    p = _get_project(pid)
    await run_in_threadpool(manager.cancel_project, pid)
    up = (p.get("options") or {}).get("upload_path")
    if up and Path(up).parent == UPLOADS:
        Path(up).unlink(missing_ok=True)
    await run_in_threadpool(_rmtree, workspace_dir(pid))
    await run_in_threadpool(_rmtree, output_dir(pid))
    db.delete_project(pid)
    return {"ok": True}


@app.post("/api/projects/{pid}/resume")
def resume_project(pid: str):
    _get_project(pid)
    return {"job": manager.submit(pid, "process")}


@app.post("/api/projects/{pid}/cancel")
def cancel_project(pid: str):
    _get_project(pid)
    for j in db.list_jobs(pid, active_only=True):
        manager.cancel(j["id"])
    return {"ok": True}


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    if not manager.cancel(jid):
        _err(404, "Job introuvable ou déjà terminé")
    return {"ok": True}


def _state(pid: str, since: int) -> dict:
    p = db.get_project(pid)
    if not p:
        return {"gone": True}
    jobs = db.list_jobs(pid)
    view, focus = [], None
    queued_all = [j["id"] for j in db.list_jobs(active_only=True) if j["status"] == "queued"]
    for j in jobs[-12:]:
        live = manager.live_state(j["id"]) if j["status"] == "running" else None
        item = {
            "id": j["id"], "type": j["type"], "clip_id": j.get("clip_id"), "status": j["status"],
            "step": (live or j).get("step") or "", "step_pct": (live or {}).get("step_pct", 0),
            "pct": (live or {}).get("pct", j.get("progress") or 0), "message": (live or j).get("message") or "",
            "error": j.get("error"),
            "queue_pos": queued_all.index(j["id"]) + 1 if j["id"] in queued_all else None,
        }
        view.append(item)
        if j["status"] == "running" or focus is None or j["type"] == "process":
            focus = j
    logs: list[dict] = []
    if focus:
        live = manager.live_state(focus["id"])
        if live:
            logs = [l for l in live["logs"] if l["seq"] > since]
        elif since == 0:
            f = workspace_dir(pid) / "logs" / f"{focus['id']}.log"
            if f.exists():
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()[-150:]
                logs = [{"seq": -1, "t": l[:8], "level": l[9:16].strip().lower(), "msg": l[17:]} for l in lines]
    clips = db.list_clips(pid)
    rev = hashlib.md5(json.dumps([(c["id"], c.get("render_status"), c.get("file"), c.get("updated_at")) for c in clips]).encode()).hexdigest()[:10]
    return {
        "project": {"id": pid, "status": p["status"], "error": p.get("error"), "title": p.get("title")},
        "jobs": view, "log_job": focus["id"] if focus else None, "logs": logs, "clips_rev": rev,
    }


@app.get("/api/projects/{pid}/events")
async def events(pid: str, request: Request):
    _get_project(pid)

    async def gen():
        since, last, last_beat = 0, None, time.time()
        log_job = None
        file_logs_sent = None
        while True:
            if await request.is_disconnected():
                break
            st = await run_in_threadpool(_state, pid, since)
            if st.get("gone"):
                yield "event: gone\ndata: {}\n\n"
                break
            if st["log_job"] != log_job:
                log_job = st["log_job"]
                if since:
                    since = 0
                    st = await run_in_threadpool(_state, pid, 0)
            if st["logs"] and all(l["seq"] == -1 for l in st["logs"]):
                # journal relu depuis le fichier : envoyé une seule fois par job
                if file_logs_sent == log_job:
                    st["logs"] = []
                else:
                    file_logs_sent = log_job
            if st["logs"]:
                since = max(since, max(l["seq"] for l in st["logs"]))
            payload = json.dumps(st, ensure_ascii=False)
            if payload != last or st["logs"]:
                last = payload
                yield f"event: state\ndata: {payload}\n\n"
                last_beat = time.time()
            elif time.time() - last_beat > 15:
                yield ": ping\n\n"
                last_beat = time.time()
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/projects/{pid}/timeline")
def timeline(pid: str):
    p = _get_project(pid)
    wd = workspace_dir(pid)
    prep = read_json(wd / "prepare.json") or {}
    tr = read_json(wd / "transcript.json") or {}
    au = read_json(wd / "audio.json") or {}
    sc = read_json(wd / "scenes.json") or {}
    excite = au.get("excite") or []
    step = max(1, len(excite) // 1500)
    sprite = dict(prep.get("sprite") or {})
    if sprite:
        sprite["url"] = f"/media/{pid}/thumbs.jpg"
    return {
        "duration": p.get("duration") or (prep.get("probe") or {}).get("duration") or 0,
        "sprite": sprite or None,
        "words": [[w["s"], w["e"], w["t"]] for w in tr.get("words", [])],
        "audio": {"hop": au.get("hop", 0.5) * step, "values": [max(excite[i : i + step]) for i in range(0, len(excite), step)]},
        "cuts": sc.get("cuts") or [],
    }


@app.put("/api/projects/{pid}/facecam")
async def set_facecam(pid: str, request: Request):
    p = _get_project(pid)
    body = await request.json()
    fc = dict(p.get("facecam") or {})
    if body.get("reset"):
        det = read_json(workspace_dir(pid) / "facecam.json") or {"found": False}
        det.pop("_params", None)
        fc = det
    else:
        try:
            x, y, w, h = [int(round(float(v))) for v in body["rect"]]
        except (KeyError, TypeError, ValueError):
            _err(400, "Rectangle invalide.")
        W, H = int(p.get("width") or 0), int(p.get("height") or 0)
        x, y = max(0, min(x, W - 16)), max(0, min(y, H - 16))
        w, h = max(16, min(w, W - x)), max(16, min(h, H - y))
        fc.update({"found": True, "rect": [x, y, w, h], "manual": True})
        fc.pop("face", None)
    db.update_project(pid, facecam=fc)
    for c in db.list_clips(pid):
        if c.get("layout") == "facecam":
            db.update_clip(c["id"], dirty=1)
    return {"facecam": fc}


@app.post("/api/projects/{pid}/clips")
async def add_clip(pid: str, request: Request):
    p = _get_project(pid)
    body = await request.json()
    try:
        s, e = float(body["start"]), float(body["end"])
    except (KeyError, TypeError, ValueError):
        _err(400, "Début / fin invalides.")
    dur = float(p.get("duration") or 0)
    s, e = max(0.0, s), min(dur or e, e)
    if e - s < 1:
        _err(400, "Le clip doit durer au moins 1 seconde.")
    if e - s > 180:
        _err(400, "Un clip ne peut pas dépasser 3 minutes.")
    opts = tasks.project_options(p)
    tr = read_json(workspace_dir(pid) / "transcript.json") or {}
    from clipper.reframe import resolve_layout

    layout = body.get("layout") if body.get("layout") in LAYOUTS else resolve_layout(opts["layout"], p.get("facecam"))
    clip = db.create_clip(
        pid, rank=999, manual=1, start=round(s, 3), end=round(e, 3), score=0, signals={},
        hook=str(body.get("hook") or ""), reason="Clip ajouté manuellement", layout=layout,
        preset=opts["subtitle_preset"], show_hook=1 if opts["hook_title"] else 0,
        words=tasks.clip_words_from_transcript(tr, s, e), title="", description="", hashtags=[],
        render_status="pending", dirty=1,
    )
    job = manager.submit(pid, "render", clip["id"])
    return {"clip": _clip_summary(clip), "job": job}


@app.post("/api/projects/{pid}/render-all")
def render_all(pid: str):
    _get_project(pid)
    jobs = [manager.submit(pid, "render", c["id"]) for c in db.list_clips(pid) if c.get("dirty") or not c.get("file")]
    return {"jobs": jobs}


@app.get("/api/projects/{pid}/export.zip")
async def export(pid: str):
    _get_project(pid)
    z = await run_in_threadpool(tasks.export_zip, pid)
    return FileResponse(z, filename=z.name, media_type="application/zip")


# --------------------------------------------------------------------------
# Clips
# --------------------------------------------------------------------------

def _get_clip(cid: str) -> dict:
    c = db.get_clip(cid) if SAFE_NAME.match(cid) else None
    if not c:
        _err(404, "Clip introuvable")
    return c


def _clip_view(c: dict) -> dict:
    p = db.get_project(c["project_id"]) or {}
    W, H = int(p.get("width") or 1920), int(p.get("height") or 1080)
    fc = p.get("facecam") if (p.get("facecam") or {}).get("rect") else None
    geom = layout_geometry(c.get("layout") or "auto", W, H, fc)
    start, end = float(c["start"]), float(c["end"])
    words = clip_words(c.get("words") or [], start, end)
    out = _clip_summary(c)
    out["words"] = c.get("words") or []
    out["geometry"] = geom
    out["captions"] = caption_payload(words, c.get("preset"), geom, c.get("hook") or "", bool(c.get("show_hook")), end - start)
    tr = read_json(workspace_dir(c["project_id"]) / "render" / c["id"] / "track.json")
    out["track"] = tr["points"] if tr and tr.get("start") == start and tr.get("end") == end else None
    out["facecam"] = p.get("facecam")
    out["project_duration"] = p.get("duration")
    return out


@app.get("/api/clips/{cid}")
def get_clip(cid: str):
    return _clip_view(_get_clip(cid))


@app.patch("/api/clips/{cid}")
async def patch_clip(cid: str, request: Request):
    c = _get_clip(cid)
    body = await request.json()
    upd: dict = {}
    p = db.get_project(c["project_id"]) or {}
    dur = float(p.get("duration") or 0)
    if "start" in body or "end" in body:
        try:
            s = float(body.get("start", c["start"]))
            e = float(body.get("end", c["end"]))
        except (TypeError, ValueError):
            _err(400, "Début / fin invalides.")
        s, e = max(0.0, s), (min(dur, e) if dur else e)
        if e - s < 1:
            _err(400, "Le clip doit durer au moins 1 seconde.")
        if e - s > 180:
            _err(400, "Un clip ne peut pas dépasser 3 minutes.")
        upd["start"], upd["end"] = round(s, 3), round(e, 3)
        tr = read_json(workspace_dir(c["project_id"]) / "transcript.json") or {}
        upd["words"] = tasks.clip_words_from_transcript(tr, s, e, body.get("words") or c.get("words"))
    elif "words" in body:
        words = []
        for w in body["words"] or []:
            try:
                item = {"i": int(w["i"]), "s": float(w["s"]), "e": float(w["e"]), "t": str(w.get("t", ""))[:60], "kw": bool(w.get("kw"))}
            except (KeyError, TypeError, ValueError):
                continue
            if w.get("del") or not item["t"].strip():
                item["del"] = True
            words.append(item)
        upd["words"] = words
    if "layout" in body:
        if body["layout"] not in LAYOUTS:
            _err(400, "Layout inconnu.")
        upd["layout"] = body["layout"]
    if "preset" in body:
        upd["preset"] = get_preset(body["preset"])[0]
    for k, n in (("hook", 90), ("title", 120), ("description", 2200)):
        if k in body:
            upd[k] = str(body[k] or "")[:n]
    if "show_hook" in body:
        upd["show_hook"] = 1 if body["show_hook"] else 0
    if "hashtags" in body:
        tags = body["hashtags"]
        if isinstance(tags, str):
            tags = tags.split()
        upd["hashtags"] = ["#" + re.sub(r"[^\w]", "", str(t).lstrip("#")) for t in tags if re.sub(r"[^\w]", "", str(t))][:15]
    visual = {"start", "end", "words", "layout", "preset", "hook", "show_hook"}
    if visual & upd.keys():
        upd["dirty"] = 1
    db.update_clip(cid, **upd)
    return _clip_view(db.get_clip(cid))


@app.post("/api/clips/{cid}/render")
def render(cid: str):
    c = _get_clip(cid)
    db.update_clip(cid, render_status="queued")
    return {"job": manager.submit(c["project_id"], "render", cid)}


@app.delete("/api/clips/{cid}")
def delete_clip(cid: str):
    c = _get_clip(cid)
    out = output_dir(c["project_id"])
    for k in ("file", "thumb"):
        if c.get(k):
            (out / c[k]).unlink(missing_ok=True)
    shutil.rmtree(workspace_dir(c["project_id"]) / "render" / cid, ignore_errors=True)
    db.delete_clip(cid)
    return {"ok": True}


@app.get("/api/clips/{cid}/download")
def download_clip(cid: str):
    c = _get_clip(cid)
    if not c.get("file"):
        _err(404, "Ce clip n'est pas encore rendu.")
    f = output_dir(c["project_id"]) / c["file"]
    if not f.exists():
        _err(404, "Fichier introuvable : relance le rendu.")
    return FileResponse(f, filename=f.name, media_type="video/mp4")


@app.get("/api/clips/{cid}/track")
async def clip_track(cid: str):
    c = _get_clip(cid)
    pid = c["project_id"]
    prep = read_json(workspace_dir(pid) / "prepare.json")
    p = db.get_project(pid) or {}
    src = tasks.source_path(pid, p)
    if not prep or not src:
        _err(400, "Projet non préparé.")
    start, end = float(c["start"]), float(c["end"])
    rdir = workspace_dir(pid) / "render" / cid
    cached = read_json(rdir / "track.json")
    if cached and cached.get("start") == start and cached.get("end") == end:
        return {"points": cached["points"]}

    def compute():
        ctx = Context(pid)
        motion = read_json(workspace_dir(pid) / "motion.json")
        pts = compute_track(ctx, src, prep["probe"], start, end, motion)
        rdir.mkdir(parents=True, exist_ok=True)
        write_json(rdir / "track.json", {"start": start, "end": end, "points": pts})
        return pts

    return {"points": await run_in_threadpool(compute)}
