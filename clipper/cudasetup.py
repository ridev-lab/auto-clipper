"""Rend les DLL CUDA (cuBLAS / cuDNN installées par pip) visibles pour
ctranslate2 / faster-whisper, sans PyTorch ni CUDA Toolkit.

À appeler AVANT `import faster_whisper`."""
from __future__ import annotations

import os
import site
import sys
from pathlib import Path

from .config import ROOT

_done = False
_dirs: list[str] = []


def cuda_dll_dirs() -> list[Path]:
    dirs: list[Path] = []
    roots = [Path(p) for p in site.getsitepackages()]
    try:
        roots.append(Path(site.getusersitepackages()))
    except Exception:
        pass
    for r in roots:
        nv = r / "nvidia"
        if nv.is_dir():
            dirs += [d for d in sorted(nv.glob("*/bin")) if d.is_dir()]
    local = ROOT / "bin"  # solution de secours : DLL copiées à la main
    if local.is_dir():
        dirs.append(local)
    return dirs


def register_cuda_dlls() -> list[str]:
    global _done
    if _done or sys.platform != "win32":
        return _dirs
    for d in cuda_dll_dirs():
        try:
            os.add_dll_directory(str(d))
        except OSError:
            continue
        _dirs.append(str(d))
    if _dirs:
        os.environ["PATH"] = os.pathsep.join(_dirs) + os.pathsep + os.environ.get("PATH", "")
    _done = True
    return _dirs


def cuda_status() -> dict:
    """Vérifie que ctranslate2 voit le GPU et que cuBLAS/cuDNN se chargent."""
    import ctypes

    register_cuda_dlls()
    info = {"ok": False, "devices": 0, "compute_types": [], "missing": [], "message": ""}
    for dll in ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn_ops64_9.dll", "cudnn_cnn64_9.dll"):
        try:
            ctypes.WinDLL(dll)
        except OSError:
            info["missing"].append(dll)
    try:
        import ctranslate2

        info["devices"] = ctranslate2.get_cuda_device_count()
        if info["devices"]:
            info["compute_types"] = sorted(ctranslate2.get_supported_compute_types("cuda"))
    except Exception as e:  # pragma: no cover
        info["message"] = f"ctranslate2 : {e}"
    info["ok"] = info["devices"] > 0 and not info["missing"]
    if not info["ok"] and not info["message"]:
        if not info["devices"]:
            info["message"] = "Aucun GPU CUDA détecté (pilote NVIDIA ?)"
        else:
            info["message"] = "DLL manquantes : " + ", ".join(info["missing"])
    return info
