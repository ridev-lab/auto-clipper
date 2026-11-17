"""Démarre le serveur : python -m backend"""
from __future__ import annotations

import socket
import sys
import threading
import webbrowser

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) != 0


def main() -> int:
    import uvicorn

    from clipper import llm
    from clipper.config import get_config
    from clipper.utils import setup_logging

    setup_logging()
    cfg = get_config()["server"]
    host, port = cfg["host"], int(cfg["port"])
    url = f"http://localhost:{port}"
    if not port_free(host, port):
        print(f"\n  Le port {port} est déjà utilisé : Auto Clipper est peut-être déjà lancé.")
        print(f"  Ouvre {url} ou change « port » dans config.yaml.\n")
        if cfg.get("open_browser", True):
            webbrowser.open(url)
        return 1
    # Ollama démarre en arrière-plan pour ne pas retarder l'interface
    threading.Thread(target=llm.ensure_running, daemon=True).start()
    if cfg.get("open_browser", True) and "--no-browser" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"\n  Auto Clipper : {url}\n  (ferme cette fenêtre pour arrêter le serveur)\n")
    uvicorn.run("backend.app:app", host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
