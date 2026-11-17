"""Client Ollama local : démarrage auto, sorties JSON structurées, libération
de la VRAM après usage."""
from __future__ import annotations

import json
import subprocess
import time

import httpx

from .config import find_tool, get_config
from .utils import CREATE_NO_WINDOW, ClipperError, Context, log


def _cfg() -> dict:
    return get_config()["ollama"]


def _url(path: str) -> str:
    return _cfg()["url"].rstrip("/") + path


def is_running(timeout: float = 2.0) -> bool:
    try:
        return httpx.get(_url("/api/version"), timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def version() -> str | None:
    try:
        return httpx.get(_url("/api/version"), timeout=2).json().get("version")
    except (httpx.HTTPError, ValueError):
        return None


def list_models() -> list[str]:
    try:
        data = httpx.get(_url("/api/tags"), timeout=5).json()
        return [m.get("name", "") for m in data.get("models", [])]
    except (httpx.HTTPError, ValueError):
        return []


def has_model(model: str | None = None) -> bool:
    model = model or _cfg()["model"]
    names = list_models()
    base = model if ":" in model else model + ":latest"
    return model in names or base in names


def ensure_running(wait: float = 25.0) -> bool:
    """Lance `ollama serve` en arrière-plan si le serveur ne répond pas."""
    if is_running():
        return True
    exe = find_tool("ollama")
    if not exe:
        return False
    log.info("Démarrage d'Ollama…")
    try:
        subprocess.Popen(
            [exe, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW | 0x00000008,  # DETACHED_PROCESS
        )
    except OSError:
        return False
    t0 = time.time()
    while time.time() - t0 < wait:
        if is_running():
            return True
        time.sleep(0.5)
    return False


def unload_model(ctx: Context | None = None) -> None:
    """Décharge le modèle de la VRAM (avant Whisper ou le rendu)."""
    if not is_running(0.5):
        return
    try:
        httpx.post(_url("/api/generate"), json={"model": _cfg()["model"], "keep_alive": 0}, timeout=15)
    except httpx.HTTPError:
        pass


def available(ctx: Context | None = None) -> tuple[bool, str]:
    """(ok, message) : Ollama lancé et modèle présent."""
    if not ensure_running():
        return False, "Ollama n'est pas lancé (et n'a pas pu être démarré automatiquement)."
    model = _cfg()["model"]
    if not has_model(model):
        return False, f"Le modèle {model} n'est pas installé : lance « ollama pull {model} »."
    return True, ""


def chat_json(system: str, user: str, schema: dict, temperature: float | None = None, retries: int = 1) -> dict:
    """Appel chat avec sortie structurée (format = schéma JSON). Une relance
    en cas de réponse invalide, puis ClipperError."""
    c = _cfg()
    body = {
        "model": c["model"],
        "stream": False,
        "format": schema,
        "keep_alive": "5m",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "options": {
            "temperature": c["temperature"] if temperature is None else temperature,
            "num_ctx": int(c["num_ctx"]),
        },
    }
    if c.get("think") is not None:
        body["think"] = bool(c["think"])
    last = ""
    for attempt in range(retries + 1):
        try:
            r = httpx.post(_url("/api/chat"), json=body, timeout=float(c["timeout"]))
        except httpx.TimeoutException:
            last = "délai dépassé"
            continue
        except httpx.HTTPError as e:
            raise ClipperError(f"Ollama ne répond pas : {e}") from None
        if r.status_code == 404:
            raise ClipperError(f"Modèle Ollama introuvable : {c['model']}")
        if r.status_code >= 400:
            last = r.text[:300]
            if "memory" in last.lower():
                raise ClipperError("Mémoire GPU insuffisante pour le modèle Ollama.")
            continue
        content = (r.json().get("message") or {}).get("content", "")
        try:
            return json.loads(content)
        except ValueError:
            # dernier recours : extraire le premier objet JSON du texte
            start, end = content.find("{"), content.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(content[start : end + 1])
                except ValueError:
                    pass
            last = "réponse JSON invalide"
            body["options"]["temperature"] = 0.0
    raise ClipperError(f"Réponse du LLM inexploitable ({last}).")
