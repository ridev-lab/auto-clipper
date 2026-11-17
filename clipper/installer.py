"""Installation (appelée par setup.bat). Uniquement la bibliothèque standard
jusqu'à l'installation des dépendances. Relançable sans risque."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
OK, WARN, ERR = "[OK]", "[!] ", "[ERREUR]"


def say(tag: str, msg: str) -> None:
    print(f"  {tag:8} {msg}", flush=True)


def title(msg: str) -> None:
    print(f"\n• {msg}", flush=True)


def registry_paths() -> list[str]:
    import winreg

    out = []
    for hive, sub in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(hive, sub) as k:
                out += [os.path.expandvars(p) for p in winreg.QueryValueEx(k, "Path")[0].split(";") if p]
        except OSError:
            pass
    return out


def find(name: str) -> str | None:
    p = shutil.which(name)
    if p:
        return p
    exe = name + ".exe"
    for d in registry_paths():
        if (Path(d) / exe).exists():
            return str(Path(d) / exe)
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    cands = [local / "Microsoft" / "WinGet" / "Links" / exe, local / "Programs" / "Ollama" / exe]
    cands += sorted((local / "Microsoft" / "WinGet" / "Packages").glob(f"*/*/bin/{exe}"), reverse=True)
    cands += [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "nodejs" / exe]
    for c in cands:
        if c.exists():
            return str(c)
    return None


def winget_install(pkg: str, label: str) -> bool:
    if not shutil.which("winget"):
        say(ERR, f"winget est absent : installe {label} à la main puis relance setup.bat")
        return False
    say("", f"Installation de {label} via winget (peut prendre quelques minutes)…")
    r = subprocess.run(["winget", "install", "-e", "--id", pkg, "--accept-source-agreements",
                        "--accept-package-agreements", "--disable-interactivity"])
    return r.returncode == 0


def ensure_tool(name: str, pkg: str, label: str, required: bool = True) -> str | None:
    p = find(name)
    if not p:
        winget_install(pkg, label)
        p = find(name)
    if p:
        os.environ["PATH"] = str(Path(p).parent) + os.pathsep + os.environ.get("PATH", "")
        say(OK, f"{label} : {p}")
    else:
        say(ERR if required else WARN, f"{label} introuvable après installation : ouvre une nouvelle fenêtre et relance setup.bat")
    return p


def pip_install() -> bool:
    title("Dépendances Python (≈3 Go, la première fois seulement)")
    subprocess.run([PY, "-m", "pip", "install", "--upgrade", "pip", "-q"])
    r = subprocess.run([PY, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])
    if r.returncode != 0:
        say(ERR, "pip n'a pas pu installer les dépendances (connexion internet ?)")
        return False
    say(OK, "dépendances installées")
    return True


def ollama_model() -> str:
    txt = (ROOT / "config.yaml").read_text(encoding="utf-8")
    m = re.search(r"^ollama:\s*\n(?:\s+.*\n)*?\s+model:\s*([^\s#]+)", txt, re.M)
    return m.group(1).strip("\"'") if m else "qwen2.5:7b"


def ollama_running() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2):
            return True
    except Exception:
        return False


def ensure_ollama(exe: str) -> bool:
    if not ollama_running():
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=0x08000000 | 0x00000008)
        for _ in range(40):
            if ollama_running():
                break
            time.sleep(0.5)
    if not ollama_running():
        say(ERR, "Ollama ne démarre pas : lance l'application Ollama puis relance setup.bat")
        return False
    model = ollama_model()
    listed = subprocess.run([exe, "list"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    if model.split(":")[0] in listed and (":" not in model or model in listed):
        say(OK, f"modèle IA {model} déjà présent")
        return True
    say("", f"Téléchargement du modèle IA {model} (≈5 Go)…")
    r = subprocess.run([exe, "pull", model])
    if r.returncode != 0:
        say(ERR, f"échec du téléchargement de {model}")
        return False
    say(OK, f"modèle IA {model} installé")
    return True


def main() -> int:
    os.environ["PYTHONUTF8"] = "1"
    print(f"\n  Python {sys.version.split()[0]} — {PY}")
    if sys.version_info[:2] != (3, 12):
        say(WARN, "version testée : Python 3.12")

    title("Outils système")
    ff = ensure_tool("ffmpeg", "Gyan.FFmpeg", "ffmpeg (build complète avec NVENC et libass)")
    ol = ensure_tool("ollama", "Ollama.Ollama", "Ollama (IA locale)")
    node = find("node") or find("deno")
    if node:
        say(OK, f"runtime JavaScript pour YouTube : {node}")
    else:
        ensure_tool("deno", "DenoLand.Deno", "Deno (runtime JavaScript pour YouTube)", required=False)

    if not pip_install():
        return 1

    title("Modèle IA (Ollama)")
    ok_ollama = ensure_ollama(ol) if ol else False

    title("Ressources (polices, détection de visage, Whisper)")
    r = subprocess.run([PY, "-m", "clipper.assets"], cwd=ROOT)

    title("Diagnostic")
    d = subprocess.run([PY, str(ROOT / "clipper.py"), "doctor"], cwd=ROOT)
    return 0 if (ff and ok_ollama and r.returncode == 0 and d.returncode == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
