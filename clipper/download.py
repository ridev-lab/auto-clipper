"""Ingestion : téléchargement YouTube (yt-dlp) ou import d'un fichier."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .config import ffmpeg, find_tool, get_config
from .utils import Cancelled, ClipperError, Context, run_ffmpeg

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
YOUTUBE_RE = re.compile(
    r"^(https?://)?(www\.|m\.|music\.)?(youtube\.com/(watch\?|shorts/|live/|embed/)|youtu\.be/)\S+",
    re.I,
)


def is_youtube_url(text: str) -> bool:
    return bool(YOUTUBE_RE.match((text or "").strip()))


def _js_runtimes() -> dict:
    name = (get_config()["tools"].get("js_runtime") or "node").lower()
    path = find_tool(name)
    if not path and name != "node":
        name, path = "node", find_tool("node")
    if not path:
        return {}
    return {name: {"path": path}}


def _base_opts() -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 10,
    }
    runtimes = _js_runtimes()
    if runtimes:
        opts["js_runtimes"] = runtimes
    ff = find_tool("ffmpeg")
    if ff:
        opts["ffmpeg_location"] = str(Path(ff).parent)
    return opts


def _friendly_error(e: Exception) -> ClipperError:
    msg = str(e)
    low = msg.lower()
    if "private" in low:
        return ClipperError("Cette vidéo YouTube est privée.")
    if "sign in to confirm your age" in low or "age-restricted" in low:
        return ClipperError("Vidéo soumise à une restriction d'âge : impossible de la télécharger sans compte.")
    if "unavailable" in low or "not available" in low:
        return ClipperError("Vidéo YouTube indisponible (supprimée, bloquée dans ton pays ou lien invalide).")
    if "is not a valid url" in low or "unsupported url" in low:
        return ClipperError("Lien YouTube invalide.")
    if "live event will begin" in low or "is live" in low:
        return ClipperError("Ce live n'est pas terminé : attends la fin de la diffusion.")
    if "javascript" in low or "js runtime" in low or "n challenge" in low:
        return ClipperError(
            "yt-dlp n'arrive pas à résoudre la protection de YouTube. Mets-le à jour "
            "(relance start.bat) et vérifie que Node est installé."
        )
    first = msg.splitlines()[0] if msg else type(e).__name__
    return ClipperError(f"Échec du téléchargement YouTube : {first[:300]}")


def youtube_info(url: str) -> dict:
    import yt_dlp

    if not is_youtube_url(url):
        raise ClipperError("Ce n'est pas un lien YouTube valide.")
    try:
        with yt_dlp.YoutubeDL({**_base_opts(), "skip_download": True}) as ydl:
            info = ydl.extract_info(url.strip(), download=False)
    except yt_dlp.utils.DownloadError as e:
        raise _friendly_error(e) from None
    return {
        "id": info.get("id"),
        "title": info.get("title") or "Vidéo YouTube",
        "duration": info.get("duration") or 0,
        "thumbnail": info.get("thumbnail"),
        "uploader": info.get("uploader") or info.get("channel") or "",
        "is_live": bool(info.get("is_live")),
    }


def download_youtube(
    ctx: Context, url: str, dest_dir: Path, range_start: float | None = None, range_end: float | None = None
) -> tuple[Path, dict]:
    """Télécharge en meilleure qualité <= max_height (MP4) dans un processus
    yt-dlp séparé (annulable à tout moment), puis découpe la plage demandée
    localement. Le téléchargement « par plage » de yt-dlp passe par ffmpeg,
    que YouTube bride fortement : on télécharge donc tout, c'est plus fiable."""
    import sys

    from .utils import run_process

    max_h = int(get_config()["download"]["max_height"])
    dest_dir.mkdir(parents=True, exist_ok=True)
    fmt = (
        f"bv*[height<={max_h}][vcodec^=avc1]+ba[ext=m4a]/"
        f"bv*[height<={max_h}]+ba/b[height<={max_h}]/b"
    )
    out_name = "download" if (range_start or range_end) else "source"
    cmd = [
        sys.executable, "-m", "yt_dlp", url.strip(),
        "--no-playlist", "--newline", "--no-colors", "--no-simulate", "--progress", "--force-overwrites",
        "-f", fmt, "--merge-output-format", "mp4", "-o", str(dest_dir / f"{out_name}.%(ext)s"),
        "-N", "4", "--retries", "5", "--fragment-retries", "10", "--socket-timeout", "30",
        "--progress-template", "download:[PROG] %(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|%(progress.speed)s",
        "--print", "before_dl:[TITLE] %(title)s", "--print", "before_dl:[UPLOADER] %(uploader)s",
    ]
    for name, conf in _js_runtimes().items():
        cmd += ["--js-runtimes", f"{name}:{conf['path']}" if conf.get("path") else name]
    ff = find_tool("ffmpeg")
    if ff:
        cmd += ["--ffmpeg-location", str(Path(ff).parent)]

    meta = {"title": "", "uploader": ""}
    state = {"file": 0, "last_done": 0}

    def num(v: str) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    def on_line(line: str):
        if line.startswith("[TITLE] "):
            meta["title"] = line[8:].strip()
        elif line.startswith("[UPLOADER] "):
            meta["uploader"] = line[11:].strip()
        elif line.startswith("[PROG] "):
            parts = line[7:].split("|")
            if len(parts) < 4:
                return
            done, total, est, speed = (num(p) for p in parts[:4])
            total = total or est
            if done < state["last_done"] * 0.5:  # nouveau fichier (audio après vidéo)
                state["file"] += 1
            state["last_done"] = done
            if total:
                frac = min(1.0, done / total)
                pct = frac * 90 if state["file"] == 0 else 90 + frac * 7
                ctx.progress(pct, f"{done / 1e6:.0f} / {total / 1e6:.0f} Mo" + (f" — {speed / 1e6:.1f} Mo/s" if speed else ""))
        elif "[Merger]" in line or "Merging formats" in line:
            ctx.progress(97, "Assemblage audio + vidéo")

    try:
        run_process(cmd, ctx, on_line=on_line)
    except ClipperError as e:
        raise _friendly_error(Exception(_last_error_line(str(e)))) from None
    files = sorted(dest_dir.glob(f"{out_name}.*"), key=lambda p: p.stat().st_size, reverse=True)
    files = [f for f in files if f.suffix.lower() in VIDEO_EXTS]
    if not files:
        raise ClipperError("Téléchargement terminé mais aucun fichier vidéo trouvé.")
    src = files[0]
    if out_name == "download":
        ctx.progress(98, "Découpe de la plage demandée")
        src = trim(ctx, src, dest_dir / "source.mp4", range_start, range_end)
        files[0].unlink(missing_ok=True)  # la vidéo complète n'est plus utile
    return src, meta


def _last_error_line(text: str) -> str:
    lines = [l for l in text.splitlines() if "ERROR" in l]
    return (lines[-1] if lines else text.splitlines()[-1] if text else "").replace("ERROR:", "").strip()


def trim(ctx: Context, src: Path, out: Path, range_start: float | None, range_end: float | None) -> Path:
    """Découpe sans réencodage (quasi instantané)."""
    args = []
    if range_start:
        args += ["-ss", f"{range_start:.3f}"]
    args += ["-i", str(src)]
    if range_end:
        args += ["-t", f"{max(1.0, range_end - (range_start or 0)):.3f}"]
    args += ["-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-avoid_negative_ts", "make_zero", str(out)]
    run_ffmpeg(args, ctx)
    return out


def import_file(
    ctx: Context, src: Path, dest_dir: Path, range_start: float | None = None,
    range_end: float | None = None, move: bool = True,
) -> Path:
    """Place le fichier dans le projet (déplacement pour un upload, lien dur
    ou copie pour un fichier local de la CLI) ; découpe la plage demandée
    (copie de flux, quasi instantané) si nécessaire."""
    import os

    src = Path(src)
    if src.suffix.lower() not in VIDEO_EXTS:
        raise ClipperError(f"Format non supporté : {src.suffix}. Formats acceptés : MP4, MOV, MKV, WEBM.")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"original{src.suffix.lower()}"
    if src.resolve() != dest.resolve():
        if dest.exists():
            dest.unlink()
        if move:
            shutil.move(str(src), dest)
        else:
            ctx.progress(5, "Copie du fichier dans le projet")
            try:
                os.link(src, dest)  # instantané, sans espace disque en plus
            except OSError:
                shutil.copy2(src, dest)
    if range_start is None and range_end is None:
        return dest
    ctx.progress(10, "Découpe de la plage demandée")
    return trim(ctx, dest, dest_dir / f"source{src.suffix.lower()}", range_start, range_end)


def check_ffmpeg_ready() -> None:
    ffmpeg()  # lève une erreur explicite si absent
