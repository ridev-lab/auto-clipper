"""Outils communs : contexte d'exécution (progression, logs, annulation),
lancement de processus, JSON, cache par étape."""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path
from typing import Callable, Iterable

from .config import get_config, output_dir, workspace_dir

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

log = logging.getLogger("clipper")

# Étapes affichées dans l'interface (ordre du pipeline)
STEPS: list[tuple[str, str]] = [
    ("ingest", "Téléchargement / import"),
    ("prepare", "Préparation (audio + proxy)"),
    ("transcribe", "Transcription"),
    ("audio", "Analyse audio"),
    ("scenes", "Scènes et mouvement"),
    ("scoring", "Scoring IA"),
    ("reframe", "Recadrage"),
    ("render", "Rendu"),
]
STEP_LABELS = dict(STEPS)

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class ClipperError(Exception):
    """Erreur dont le message est destiné à l'utilisateur (en français)."""


class Cancelled(Exception):
    """Traitement annulé par l'utilisateur."""


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    root.addHandler(h)
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "numba", "faster_whisper", "multipart", "PIL", "pyscenedetect", "scenedetect"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class Context:
    """Transporte l'état d'un traitement : dossier de travail, progression,
    logs et annulation. Utilisé à l'identique par la CLI et l'API."""

    def __init__(
        self,
        project_id: str,
        on_progress: Callable[[str, float, str], None] | None = None,
        on_log: Callable[[str, str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ):
        self.project_id = project_id
        self.cfg = get_config()
        self.workdir = workspace_dir(project_id)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.outdir = output_dir(project_id)
        self.outdir.mkdir(parents=True, exist_ok=True)
        self.cancel_event = cancel_event or threading.Event()
        self._on_progress = on_progress
        self._on_log = on_log
        self.step = ""
        self.step_pct = 0.0
        self._last_emit = 0.0

    def path(self, name: str) -> Path:
        return self.workdir / name

    def log(self, msg: str, level: str = "info") -> None:
        getattr(log, level if level in ("info", "warning", "error", "debug") else "info")(
            f"[{self.project_id}] {msg}"
        )
        if self._on_log:
            self._on_log(msg, level)

    def warn(self, msg: str) -> None:
        self.log(msg, "warning")

    def set_step(self, step: str, msg: str | None = None) -> None:
        self.check_cancel()
        self.step = step
        self.step_pct = 0.0
        self.log(f"▶ {STEP_LABELS.get(step, step)}" + (f" — {msg}" if msg else ""))
        self._emit(0.0, msg or "", force=True)

    def progress(self, pct: float, msg: str = "") -> None:
        self.check_cancel()
        self.step_pct = max(0.0, min(100.0, float(pct)))
        self._emit(self.step_pct, msg)

    def _emit(self, pct: float, msg: str, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_emit < 0.25 and pct < 100:
            return
        self._last_emit = now
        if self._on_progress:
            self._on_progress(self.step, pct, msg)

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()


# --------------------------------------------------------------------------
# JSON + cache
# --------------------------------------------------------------------------

def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _norm(params) -> object:
    return json.loads(json.dumps(params, sort_keys=True, default=str))


def cached_step(ctx: Context, name: str, params: dict, fn: Callable[[], dict]) -> dict:
    """Ne recalcule une étape que si son résultat n'existe pas ou si les
    paramètres ont changé."""
    path = ctx.path(f"{name}.json")
    params = _norm(params)
    data = read_json(path)
    if isinstance(data, dict) and data.get("_params") == params:
        ctx.log(f"{name} : résultat en cache réutilisé")
        return data
    data = fn()
    data["_params"] = params
    write_json(path, data)
    return data


# --------------------------------------------------------------------------
# Processus externes
# --------------------------------------------------------------------------

def run_process(
    cmd: list[str],
    ctx: Context | None = None,
    on_line: Callable[[str], None] | None = None,
    cwd: str | Path | None = None,
    env: dict | None = None,
) -> str:
    """Lance une commande, transmet chaque ligne de stdout à on_line, gère
    l'annulation et lève ClipperError avec la fin de stderr en cas d'échec."""
    if env is None:
        # sorties des sous-processus Python en UTF-8 (sinon cp1252 sous Windows)
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [str(c) for c in cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        cwd=str(cwd) if cwd else None,
        env=env,
        creationflags=CREATE_NO_WINDOW,
    )
    lines: queue.Queue = queue.Queue()
    err_tail: list[str] = []

    def _read_out():
        for raw in iter(proc.stdout.readline, b""):
            lines.put(raw.decode("utf-8", "replace").rstrip("\r\n"))
        lines.put(None)

    def _read_err():
        for raw in iter(proc.stderr.readline, b""):
            err_tail.append(raw.decode("utf-8", "replace").rstrip())
            if len(err_tail) > 60:
                del err_tail[:20]

    t1 = threading.Thread(target=_read_out, daemon=True)
    t2 = threading.Thread(target=_read_err, daemon=True)
    t1.start()
    t2.start()
    out: list[str] = []
    try:
        while True:
            if ctx is not None and ctx.cancel_event.is_set():
                proc.kill()
                raise Cancelled()
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                break
            out.append(line)
            if on_line:
                on_line(line)
        proc.wait()
        t2.join(timeout=2)
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        raise
    if proc.returncode != 0:
        tail = "\n".join(err_tail[-12:])
        raise ClipperError(f"La commande {Path(cmd[0]).name} a échoué (code {proc.returncode}).\n{tail}")
    return "\n".join(out)


def run_ffmpeg(
    args: Iterable[str],
    ctx: Context | None = None,
    duration: float | None = None,
    cwd: str | Path | None = None,
    pct_range: tuple[float, float] = (0.0, 100.0),
    label: str = "",
) -> None:
    """ffmpeg avec suivi de progression (-progress pipe:1)."""
    from .config import ffmpeg

    cmd = [ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats", *args]
    lo, hi = pct_range

    def on_line(line: str):
        if ctx is None or not duration:
            return
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                us = int(line.split("=", 1)[1])
            except ValueError:
                return
            frac = max(0.0, min(1.0, us / 1e6 / duration))
            ctx.progress(lo + (hi - lo) * frac, label)

    run_process(cmd, ctx, on_line=on_line, cwd=cwd)


# --------------------------------------------------------------------------
# Divers
# --------------------------------------------------------------------------

def slugify(text: str, max_len: int = 40) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text[:max_len].rstrip("-")) or "clip"


def fmt_time(seconds: float) -> str:
    seconds = max(0, float(seconds))
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
