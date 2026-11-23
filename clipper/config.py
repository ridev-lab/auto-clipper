"""Chargement de config.yaml (fusionné avec les valeurs par défaut) et
résolution des chemins / outils externes."""
from __future__ import annotations

import copy
import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.yaml"
FONTS_DIR = ROOT / "fonts"
FRONTEND_DIR = ROOT / "frontend"

DEFAULTS: dict = {
    "server": {"host": "127.0.0.1", "port": 8000, "open_browser": True, "allowed_hosts": []},
    "paths": {
        "workspace": "workspace",
        "output": "output",
        "models": "models",
        "database": "data/clipper.db",
    },
    "tools": {"ffmpeg": "auto", "ffprobe": "auto", "ollama": "auto", "js_runtime": "node"},
    "defaults": {
        "clips": 5,
        "min_duration": 20,
        "max_duration": 60,
        "layout": "auto",
        "language": "auto",
        "subtitle_preset": "classique",
        "hook_title": True,
    },
    "download": {"max_height": 1080},
    "metadata": {"content_hint": "Gameplay GTA 6 (streams, vidéos de créateurs, gameplay perso)"},
    "transcription": {
        "model": "large-v3-turbo",
        "device": "cuda",
        "compute_type": "float16",
        "batch_size": 16,
        "min_speech_ratio": 0.12,
    },
    "ollama": {
        "url": "http://127.0.0.1:11434",
        "model": "qwen2.5:7b",
        "temperature": 0.2,
        "num_ctx": 8192,
        "window_minutes": 6,
        "overlap_minutes": 1,
        "timeout": 180,
        "think": None,
    },
    "scoring": {
        "weights": {"llm": 0.5, "audio": 0.2, "scenes": 0.12, "motion": 0.18},
        "weights_no_voice": {"audio": 0.45, "scenes": 0.2, "motion": 0.35},
        "signal_only_factor": 0.85,
        "boundary_margin": 0.3,
        "min_gap": 2.0,
    },
    "reframe": {
        "output_width": 1080,
        "output_height": 1920,
        "facecam_height_ratio": 0.35,
        "blur_sigma": 18,
        "blur_zoom": 1.15,
        "track_smoothing": 0.85,
    },
    "render": {
        "encoder": "auto",
        "video_bitrate": "12M",
        "max_bitrate": "16M",
        "audio_bitrate": "192k",
        "loudness": -14,
        "max_fps": 60,
    },
    "subtitle_presets": {
        "classique": {
            "label": "Classique",
            "font_file": "Montserrat-Black.ttf",
            "font_size": 96,
            "uppercase": True,
            "color": "#FFFFFF",
            "active_color": "#FFE600",
            "keyword_colors": ["#FFE600", "#3DFF6E"],
            "outline_color": "#000000",
            "outline": 7,
            "shadow": 2,
            "max_words": 3,
            "active_scale": 108,
            "y": 0.66,
        }
    },
    "hook": {
        "duration": 3.0,
        "font_file": "Montserrat-Black.ttf",
        "font_size": 70,
        "color": "#000000",
        "box_color": "#FFFFFF",
    },
}

PRESET_DEFAULTS = DEFAULTS["subtitle_presets"]["classique"]


def _deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "subtitle_presets":
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class ConfigError(Exception):
    pass


@lru_cache(maxsize=1)
def get_config() -> dict:
    data = {}
    if CONFIG_FILE.exists():
        try:
            data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:  # pragma: no cover - message utilisateur
            raise ConfigError(f"config.yaml est invalide : {e}") from e
    cfg = _deep_merge(DEFAULTS, data)
    # chaque preset hérite des valeurs par défaut pour les clés manquantes
    presets = cfg.get("subtitle_presets") or {}
    cfg["subtitle_presets"] = {
        name: {**PRESET_DEFAULTS, **(p or {})} for name, p in presets.items()
    } or copy.deepcopy(DEFAULTS["subtitle_presets"])
    return cfg


def path_of(key: str) -> Path:
    p = Path(get_config()["paths"][key])
    if not p.is_absolute():
        p = ROOT / p
    return p


def workspace_dir(project_id: str | None = None) -> Path:
    base = path_of("workspace")
    return base / project_id if project_id else base


def output_dir(project_id: str | None = None) -> Path:
    base = path_of("output")
    return base / project_id if project_id else base


def models_dir() -> Path:
    return path_of("models")


def db_path() -> Path:
    return path_of("database")


# --------------------------------------------------------------------------
# Outils externes
# --------------------------------------------------------------------------

def _registry_paths() -> list[str]:
    """PATH utilisateur + système lus dans le registre : un logiciel installé
    après l'ouverture de la console (winget) n'est pas encore dans os.environ."""
    if sys.platform != "win32":
        return []
    import winreg

    out: list[str] = []
    keys = [
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ]
    for hive, sub in keys:
        try:
            with winreg.OpenKey(hive, sub) as k:
                val, _ = winreg.QueryValueEx(k, "Path")
                out += [os.path.expandvars(x) for x in val.split(";") if x]
        except OSError:
            pass
    return out


@lru_cache(maxsize=None)
def find_tool(name: str) -> str | None:
    override = str(get_config()["tools"].get(name, "auto") or "auto")
    if override != "auto":
        return override if Path(override).exists() else None
    found = shutil.which(name)
    if found:
        return found
    exe = name + (".exe" if sys.platform == "win32" else "")
    for d in _registry_paths():
        cand = Path(d) / exe
        if cand.exists():
            return str(cand)
    local = os.environ.get("LOCALAPPDATA", "")
    extra: list[Path] = []
    if local:
        extra.append(Path(local) / "Microsoft" / "WinGet" / "Links" / exe)
        pkgs = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if pkgs.exists() and name in ("ffmpeg", "ffprobe"):
            extra += sorted(pkgs.glob(f"Gyan.FFmpeg*/*/bin/{exe}"), reverse=True)
        if name == "ollama":
            extra.append(Path(local) / "Programs" / "Ollama" / exe)
    for cand in extra:
        if cand.exists():
            return str(cand)
    return None


def ffmpeg() -> str:
    p = find_tool("ffmpeg")
    if not p:
        raise ConfigError(
            "ffmpeg est introuvable. Lance setup.bat ou installe-le avec : "
            "winget install -e --id Gyan.FFmpeg"
        )
    return p


def ffprobe() -> str:
    p = find_tool("ffprobe")
    if not p:
        raise ConfigError("ffprobe est introuvable (il est fourni avec ffmpeg). Lance setup.bat.")
    return p
