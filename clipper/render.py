"""Rendu final d'un clip : recadrage + sous-titres + audio normalisé,
en un seul passage ffmpeg."""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path

from .config import FONTS_DIR, ffmpeg, get_config
from .media import encoder_args, video_encoder
from .reframe import build_filtergraph, compute_track, layout_geometry, write_sendcmd
from .subtitles import build_ass, caption_payload, clip_words
from .utils import CREATE_NO_WINDOW, Context, read_json, run_ffmpeg, slugify, write_json


def measure_loudness(src: Path, start: float, dur: float, target: float) -> dict | None:
    cmd = [
        ffmpeg(), "-hide_banner", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
        "-vn", "-af", f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-",
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=300, creationflags=CREATE_NO_WINDOW)
    txt = res.stderr.decode("utf-8", "replace")
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", txt, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        if data.get("input_i") in ("-inf", "inf") or float(data["input_i"]) < -70:
            return None
        return data
    except (ValueError, KeyError):
        return None


def _prepare_fonts(rdir: Path) -> None:
    fdir = rdir / "fonts"
    fdir.mkdir(parents=True, exist_ok=True)
    for f in FONTS_DIR.glob("*.ttf"):
        dest = fdir / f.name
        if not dest.exists() or dest.stat().st_size != f.stat().st_size:
            shutil.copy2(f, dest)


def clip_filename(clip: dict) -> str:
    date = dt.datetime.now().strftime("%Y%m%d")
    title = clip.get("hook") or clip.get("title") or "clip"
    return f"{date}_{slugify(title)}_{int(clip.get('score') or 0)}.mp4"


def render_clip(ctx: Context, src: Path, info: dict, clip: dict, facecam: dict | None, pct_range=(0, 100)) -> dict:
    """clip : dict issu de la base (start, end, layout, preset, hook, show_hook, words)."""
    lo, hi = pct_range
    cfg = get_config()
    start, end = float(clip["start"]), float(clip["end"])
    dur = round(end - start, 3)
    rdir = ctx.path(f"render/{clip['id']}")
    rdir.mkdir(parents=True, exist_ok=True)
    _prepare_fonts(rdir)

    fc = facecam if (facecam and facecam.get("rect")) else None
    geom = layout_geometry(clip.get("layout") or "auto", info["width"], info["height"], fc)
    words = clip_words(clip.get("words") or [], start, end)
    payload = caption_payload(words, clip.get("preset"), geom, clip.get("hook") or "", bool(clip.get("show_hook")), dur)
    (rdir / "clip.ass").write_text(build_ass(payload, geom["width"], geom["height"]), encoding="utf-8")

    sendcmd = None
    if geom["layout"] == "track":
        ctx.progress(lo + (hi - lo) * 0.05, "Suivi du centre d'intérêt")
        track = clip.get("track")
        if not track or track.get("start") != start or track.get("end") != end:
            motion = read_json(ctx.path("motion.json"))
            points = compute_track(ctx, src, info, start, end, motion)
            track = {"start": start, "end": end, "points": points}
            write_json(rdir / "track.json", track)
        write_sendcmd(track["points"], rdir / "track.cmd")
        sendcmd = "track.cmd"

    fps = min(float(info["fps"]), float(cfg["render"]["max_fps"]))
    fps = 60.0 if abs(fps - 60) < 1.5 else (30.0 if abs(fps - 30) < 1.5 else round(fps, 3))
    graph = build_filtergraph(geom, fps, dur, "clip.ass", sendcmd)

    args = ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-filter_complex", graph, "-map", "[vout]"]
    if info.get("has_audio"):
        target = float(cfg["render"]["loudness"])
        ctx.progress(lo + (hi - lo) * 0.1, "Mesure du volume")
        m = measure_loudness(src, start, dur, target)
        if m:
            af = (
                f"loudnorm=I={target}:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
                f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true"
            )
        else:
            af = f"loudnorm=I={target}:TP=-1.5:LRA=11"
        args += ["-map", "0:a:0", "-af", af + ",aresample=48000", "-c:a", "aac", "-b:a", str(cfg["render"]["audio_bitrate"]), "-ar", "48000"]
    args += encoder_args("final", fps) + ["-r", f"{fps:g}", "-fps_mode", "cfr", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    tmp_out = rdir / "out.mp4"
    args.append(str(tmp_out.name))
    ctx.progress(lo + (hi - lo) * 0.15, f"Encodage ({video_encoder()})")
    run_ffmpeg(args, ctx, duration=dur, cwd=rdir, pct_range=(lo + (hi - lo) * 0.15, lo + (hi - lo) * 0.95), label=f"Encodage ({video_encoder()})")

    # fichier final + miniature
    name = clip_filename(clip)
    final = ctx.outdir / name
    old = clip.get("file")
    if old and Path(old).name != name:
        (ctx.outdir / Path(old).name).unlink(missing_ok=True)
    shutil.move(str(rdir / tmp_out.name), final)
    thumb = ctx.outdir / f"{Path(name).stem}.jpg"
    if old:
        (ctx.outdir / (Path(old).stem + ".jpg")).unlink(missing_ok=True)
    try:
        run_ffmpeg(["-ss", f"{min(1.0, dur / 2):.2f}", "-i", str(final), "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", str(thumb)])
    except Exception:
        thumb = None
    ctx.progress(hi, "Clip rendu")
    return {"file": final.name, "thumb": thumb.name if thumb else None, "duration": dur, "layout": geom["layout"]}
