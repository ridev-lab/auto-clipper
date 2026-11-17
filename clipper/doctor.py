"""Diagnostic de l'installation (CLI « doctor » et voyants de l'interface)."""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from importlib import metadata as im

from .config import FONTS_DIR, find_tool, get_config, models_dir, workspace_dir
from .utils import CREATE_NO_WINDOW


def _run(cmd: list[str], timeout: int = 15) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
        return (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
    except Exception:
        return ""


def check(level_ok: bool | None, id_: str, label: str, detail: str = "", fix: str = "") -> dict:
    status = "ok" if level_ok is True else ("warn" if level_ok is None else "error")
    return {"id": id_, "label": label, "status": status, "detail": detail, "fix": fix}


def gpu_info() -> dict | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    out = _run([exe, "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader,nounits"])
    try:
        name, total, used, drv = [x.strip() for x in out.strip().splitlines()[0].split(",")]
        return {"name": name, "vram_total": int(total), "vram_used": int(used), "driver": drv}
    except (ValueError, IndexError):
        return None


def whisper_model_ready() -> bool:
    name = get_config()["transcription"]["model"]
    repo = {"large-v3-turbo": "mobiuslabsgmbh--faster-whisper-large-v3-turbo", "turbo": "mobiuslabsgmbh--faster-whisper-large-v3-turbo"}.get(
        name, f"Systran--faster-whisper-{name}"
    )
    return any(models_dir().glob(f"models--{repo}/snapshots/*/model.bin"))


def run_checks(quick: bool = False) -> list[dict]:
    from . import llm
    from .cudasetup import cuda_status
    from .media import nvenc_available

    cfg = get_config()
    out: list[dict] = []
    v = sys.version_info
    out.append(check(v[:2] == (3, 12) or None, "python", "Python", platform.python_version(),
                     "" if v[:2] == (3, 12) else "Version testée : Python 3.12"))

    g = gpu_info()
    out.append(check(bool(g) or None, "gpu", "Carte graphique NVIDIA",
                     f"{g['name']} — {g['vram_total']} Mo, pilote {g['driver']}" if g else "nvidia-smi introuvable",
                     "" if g else "Installe le pilote NVIDIA (sinon tout tourne sur le CPU, plus lentement)"))

    cu = cuda_status()
    out.append(check(cu["ok"] or None, "cuda", "CUDA pour Whisper",
                     "GPU utilisable (float16)" if cu["ok"] else cu["message"],
                     "" if cu["ok"] else "Relance setup.bat ; voir README > Dépannage CUDA. Repli automatique sur le CPU."))

    ff = find_tool("ffmpeg")
    if ff:
        ver = _run([ff, "-hide_banner", "-version"]).splitlines()[:1]
        filters = _run([ff, "-hide_banner", "-filters"])
        has_ass = " ass " in filters
        out.append(check(True, "ffmpeg", "ffmpeg", ver[0][:60] if ver else ff))
        out.append(check(has_ass, "libass", "Sous-titres (libass)", "disponible" if has_ass else "absent",
                         "" if has_ass else "Installe la build complète : winget install -e --id Gyan.FFmpeg"))
        nv = nvenc_available()
        out.append(check(nv or None, "nvenc", "Encodeur NVENC", "h264_nvenc OK" if nv else "indisponible → libx264 (plus lent)",
                         "" if nv else "Mets à jour le pilote NVIDIA"))
    else:
        out.append(check(False, "ffmpeg", "ffmpeg", "introuvable", "winget install -e --id Gyan.FFmpeg puis relance"))

    model = cfg["ollama"]["model"]
    ol = find_tool("ollama")
    if not ol:
        out.append(check(False, "ollama", "Ollama", "non installé", "winget install -e --id Ollama.Ollama"))
    else:
        running = llm.is_running() or (not quick and llm.ensure_running())
        if not running:
            out.append(check(False, "ollama", "Ollama", "installé mais ne démarre pas", "Lance « ollama serve » dans un terminal"))
        else:
            ok = llm.has_model(model)
            out.append(check(ok, "ollama", "Ollama + modèle IA", f"v{llm.version()} — {model}" + ("" if ok else " absent"),
                             "" if ok else f"ollama pull {model}"))

    try:
        ytv = im.version("yt-dlp")
        try:
            ejs = im.version("yt-dlp-ejs")
        except im.PackageNotFoundError:
            ejs = None
        rt_name = cfg["tools"].get("js_runtime", "node")
        rt = find_tool(rt_name) or find_tool("node")
        rt_ver = _run([rt, "--version"]).strip() if rt else ""
        ok = bool(ejs and rt)
        out.append(check(ok, "ytdlp", "yt-dlp (YouTube)",
                         f"{ytv}, composant EJS {ejs or 'absent'}, runtime JS {rt_name} {rt_ver or 'absent'}",
                         "" if ok else 'pip install -U "yt-dlp[default]" et installe Node.js'))
    except im.PackageNotFoundError:
        out.append(check(False, "ytdlp", "yt-dlp (YouTube)", "non installé", "Relance setup.bat"))

    wm = whisper_model_ready()
    out.append(check(wm or None, "whisper", "Modèle Whisper", cfg["transcription"]["model"] + (" téléchargé" if wm else " pas encore téléchargé"),
                     "" if wm else "Il sera téléchargé au premier traitement (≈1,6 Go) ou via setup.bat"))
    fonts = sorted(f.name for f in FONTS_DIR.glob("*.ttf"))
    out.append(check(bool(fonts), "fonts", "Polices des sous-titres", ", ".join(fonts) or "aucune", "" if fonts else "Relance setup.bat"))

    ws = workspace_dir()
    ws.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(ws).free / 1e9
    out.append(check(True if free > 20 else (None if free > 5 else False), "disk", "Espace disque", f"{free:.0f} Go libres",
                     "" if free > 20 else "Libère de la place (les vidéos sources prennent plusieurs Go)"))
    return out


def print_report() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("\n  Diagnostic Auto Clipper\n  " + "─" * 40)
    checks = run_checks()
    icons = {"ok": "[OK]  ", "warn": "[!]   ", "error": "[ERREUR]"}
    for c in checks:
        print(f"  {icons[c['status']]:8} {c['label']:<28} {c['detail']}")
        if c["fix"] and c["status"] != "ok":
            print(f"  {'':8} → {c['fix']}")
    errors = [c for c in checks if c["status"] == "error"]
    print("  " + "─" * 40)
    if errors:
        print(f"  {len(errors)} problème(s) bloquant(s) à corriger.\n")
        return 1
    print("  Tout est prêt.\n")
    return 0
