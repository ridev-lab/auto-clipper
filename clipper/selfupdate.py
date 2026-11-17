"""Met à jour yt-dlp au plus une fois par 24 h (appelé par start.bat)."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAMP = ROOT / "data" / ".ytdlp_update"


def main() -> int:
    STAMP.parent.mkdir(parents=True, exist_ok=True)
    if STAMP.exists() and time.time() - STAMP.stat().st_mtime < 24 * 3600:
        return 0
    print("  Vérification des mises à jour de yt-dlp…", flush=True)
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-U", "-q", "--disable-pip-version-check",
         "--timeout", "8", "--retries", "1", "yt-dlp[default]"],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        STAMP.touch()
    else:
        print("  (mise à jour impossible, on continue avec la version installée)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
