"""Téléchargement des ressources : polices (licence OFL), modèle de détection
de visage YuNet (OpenCV) et modèle Whisper. Appelé par setup.bat."""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

from .config import FONTS_DIR, get_config, models_dir

FONTS = {
    "Montserrat-Black.ttf": "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Black.ttf",
    "Montserrat-ExtraBold.ttf": "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-ExtraBold.ttf",
    "Anton-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf",
}
YUNET_FILE = "face_detection_yunet_2023mar.onnx"
YUNET_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/" + YUNET_FILE


def _download(url: str, dest: Path, min_size: int) -> bool:
    if dest.exists() and dest.stat().st_size >= min_size:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "auto-clipper-setup"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 16):
                f.write(chunk)
    except Exception as e:
        print(f"  ! échec du téléchargement de {dest.name} : {e}")
        tmp.unlink(missing_ok=True)
        return False
    if tmp.stat().st_size < min_size:
        print(f"  ! fichier {dest.name} trop petit, téléchargement corrompu")
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(dest)
    return True


def yunet_path() -> Path:
    return models_dir() / YUNET_FILE


def ensure_fonts() -> bool:
    ok = True
    for name, url in FONTS.items():
        ok &= _download(url, FONTS_DIR / name, 20_000)
    return ok


def ensure_face_model() -> bool:
    return _download(YUNET_URL, yunet_path(), 100_000)


def ensure_whisper_model() -> bool:
    from faster_whisper import download_model

    name = get_config()["transcription"]["model"]
    try:
        download_model(name, cache_dir=str(models_dir()))
        return True
    except Exception as e:
        print(f"  ! échec du téléchargement du modèle Whisper : {e}")
        return False


def main() -> int:
    print("• Polices de sous-titres…")
    a = ensure_fonts()
    print("• Modèle de détection de visage (YuNet)…")
    b = ensure_face_model()
    print("• Modèle Whisper (≈1,6 Go, une seule fois)…")
    c = ensure_whisper_model()
    ok = a and b and c
    print("  ressources OK" if ok else "  certaines ressources manquent (voir ci-dessus)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
