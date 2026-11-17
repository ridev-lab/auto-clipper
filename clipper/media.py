"""ffprobe + détection de l'encodeur (NVENC ou libx264)."""
from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

from .config import ffmpeg, ffprobe, get_config
from .utils import CREATE_NO_WINDOW, ClipperError


def _rate(s: str | None) -> float:
    try:
        if not s or s == "0/0":
            return 0.0
        if "/" in s:
            a, b = s.split("/")
            return float(a) / float(b) if float(b) else 0.0
        return float(s)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: Path) -> dict:
    cmd = [ffprobe(), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise ClipperError("ffprobe ne répond pas : fichier vidéo illisible ?") from None
    if res.returncode != 0:
        raise ClipperError("Fichier vidéo illisible ou corrompu (ffprobe a échoué).")
    data = json.loads(res.stdout.decode("utf-8", "replace") or "{}")
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not v:
        raise ClipperError("Ce fichier ne contient pas de piste vidéo.")
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    rotation = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(float(sd["rotation"]))
    if abs(rotation) in (90, 270):
        w, h = h, w
    fps = _rate(v.get("avg_frame_rate")) or _rate(v.get("r_frame_rate")) or 30.0
    if fps > 240 or fps < 1:
        fps = 30.0
    duration = float(data.get("format", {}).get("duration") or v.get("duration") or 0)
    if duration <= 0:
        raise ClipperError("Impossible de déterminer la durée de la vidéo.")
    return {
        "duration": duration,
        "width": w,
        "height": h,
        "fps": round(fps, 3),
        "vcodec": v.get("codec_name"),
        "has_audio": a is not None,
        "acodec": a.get("codec_name") if a else None,
        "size": int(data.get("format", {}).get("size") or 0),
    }


@lru_cache(maxsize=1)
def nvenc_available() -> bool:
    cmd = [
        ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=c=black:s=320x240:d=0.2", "-c:v", "h264_nvenc", "-f", "null", "-",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        return res.returncode == 0
    except Exception:
        return False


def video_encoder() -> str:
    pref = str(get_config()["render"].get("encoder", "auto"))
    if pref == "libx264":
        return "libx264"
    if pref in ("auto", "h264_nvenc") and nvenc_available():
        return "h264_nvenc"
    return "libx264"


def encoder_args(kind: str = "final", fps: float = 30.0) -> list[str]:
    """Paramètres d'encodage. kind = final (publication) ou proxy (aperçu)."""
    enc = video_encoder()
    r = get_config()["render"]
    gop = str(max(1, int(round(fps))))
    if kind == "proxy":
        if enc == "h264_nvenc":
            return ["-c:v", "h264_nvenc", "-preset", "p2", "-rc", "vbr", "-cq", "27", "-b:v", "0", "-g", gop, "-bf", "0"]
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-g", gop, "-bf", "0"]
    if enc == "h264_nvenc":
        return [
            "-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "19",
            "-b:v", str(r["video_bitrate"]), "-maxrate", str(r["max_bitrate"]),
            "-bufsize", str(r["max_bitrate"]), "-profile:v", "high", "-g", str(int(round(fps)) * 2),
            "-spatial-aq", "1",
        ]
    return [
        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-maxrate", str(r["max_bitrate"]),
        "-bufsize", str(r["max_bitrate"]), "-profile:v", "high", "-g", str(int(round(fps)) * 2),
    ]
