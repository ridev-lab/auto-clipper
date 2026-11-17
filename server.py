"""Lance le serveur web d'Auto Clipper (équivalent de `python -m backend`)."""
import sys

from backend.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
