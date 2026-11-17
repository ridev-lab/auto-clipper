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
    """Télécharge en meilleure qualité <= max_height, fusionné en MP4."""
    import yt_dlp

    max_h = int(get_config()["download"]["max_height"])
    dest_dir.mkdir(parents=True, exist_ok=True)
    fmt = (
        f"bv*[height<={max_h}][vcodec^=avc1]+ba[ext=m4a]/"
        f"bv*[height<={max_h}]+ba/b[height<={max_h}]/b"
    )
    state = {"files": 0}

    def hook(d):
        if ctx.cancel_event.is_set():
            raise Cancelled()
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if total:
                # vidéo puis audio : on répartit 0-90 % puis 90-98 %
                frac = done / total
                pct = frac * 90 if state["files"] == 0 else 90 + frac * 8
                speed = d.get("speed") or 0
                ctx.progress(pct, f"{done / 1e6:.0f} / {total / 1e6:.0f} Mo" + (f" — {speed / 1e6:.1f} Mo/s" if speed else ""))
        elif d.get("status") == "finished":
            state["files"] += 1

    opts = {
        **_base_opts(),
        "format": fmt,
        "merge_output_format": "mp4",
        "outtmpl": str(dest_dir / "source.%(ext)s"),
        "progress_hooks": [hook],
        "concurrent_fragment_downloads": 4,
        "overwrites": True,
    }
    if range_start is not None or range_end is not None:
        from yt_dlp.utils import download_range_func

        opts["download_ranges"] = download_range_func(None, [(range_start or 0, range_end or float("inf"))])
        opts["force_keyframes_at_cuts"] = False
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url.strip(), download=True)
    except Cancelled:
        raise
    except yt_dlp.utils.DownloadError as e:
        if ctx.cancel_event.is_set():
            raise Cancelled() from None
        raise _friendly_error(e) from None
    files = sorted(dest_dir.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
    files = [f for f in files if f.suffix.lower() in VIDEO_EXTS]
    if not files:
        raise ClipperError("Téléchargement terminé mais aucun fichier vidéo trouvé.")
    meta = {"title": info.get("title") or "", "uploader": info.get("uploader") or "", "id": info.get("id")}
    return files[0], meta


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
    out = dest_dir / f"source{src.suffix.lower()}"
    args = []
    if range_start:
        args += ["-ss", f"{range_start:.3f}"]
    args += ["-i", str(dest)]
    if range_end:
        args += ["-t", f"{max(1.0, range_end - (range_start or 0)):.3f}"]
    args += ["-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-avoid_negative_ts", "make_zero", str(out)]
    ctx.progress(10, "Découpe de la plage demandée")
    run_ffmpeg(args, ctx)
    return out


def check_ffmpeg_ready() -> None:
    ffmpeg()  # lève une erreur explicite si absent
