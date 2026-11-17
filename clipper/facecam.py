"""Détection de la facecam (webcam incrustée) d'un streamer.

1. Détection de visages (OpenCV YuNet) sur ~24 images réparties dans la vidéo.
2. Regroupement : la facecam est un visage qui reste au même endroit.
3. Recherche du cadre de l'incrustation : bords nets présents sur la
   plupart des images (contours persistants) autour du visage.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .assets import yunet_path
from .config import ffmpeg
from .utils import CREATE_NO_WINDOW, Context, cached_step

WORK_W = 960


def grab_frame(src: Path, t: float, width: int = WORK_W):
    import cv2

    cmd = [
        ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(src),
        "-frames:v", "1", "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-",
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
    if res.returncode != 0 or not res.stdout:
        return None
    return cv2.imdecode(np.frombuffer(res.stdout, np.uint8), cv2.IMREAD_COLOR)


class FaceDetector:
    def __init__(self, size: tuple[int, int], threshold: float = 0.7):
        import cv2

        self.cv2 = cv2
        self.yunet = None
        p = yunet_path()
        if p.exists():
            self.yunet = cv2.FaceDetectorYN.create(str(p), "", size, threshold, 0.3, 50)
        else:
            self.haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    def detect(self, img) -> list[tuple[float, float, float, float, float]]:
        h, w = img.shape[:2]
        if self.yunet is not None:
            self.yunet.setInputSize((w, h))
            _, faces = self.yunet.detect(img)
            if faces is None:
                return []
            return [(float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[14])) for f in faces]
        gray = self.cv2.cvtColor(img, self.cv2.COLOR_BGR2GRAY)
        found = self.haar.detectMultiScale(gray, 1.1, 6, minSize=(24, 24))
        return [(float(x), float(y), float(a), float(b), 0.8) for (x, y, a, b) in found]


def _cluster(faces: list[tuple[int, tuple]], w: int, h: int) -> list[list[tuple[int, tuple]]]:
    clusters: list[list[tuple[int, tuple]]] = []
    for fi, f in faces:
        cx, cy = f[0] + f[2] / 2, f[1] + f[3] / 2
        for c in clusters:
            ref = c[0][1]
            rcx, rcy = ref[0] + ref[2] / 2, ref[1] + ref[3] / 2
            size_ratio = f[2] / max(1.0, ref[2])
            if abs(cx - rcx) < 0.06 * w and abs(cy - rcy) < 0.08 * h and 0.6 < size_ratio < 1.6:
                c.append((fi, f))
                break
        else:
            clusters.append([(fi, f)])
    return clusters


def _find_edge(P: np.ndarray, axis: str, rng, span: tuple[int, int], thr: float = 0.6):
    """Cherche une ligne de contour persistante (verticale si axis='x')."""
    a, b = max(0, span[0]), span[1]
    for v in rng:
        if axis == "x":
            if v < 1 or v >= P.shape[1] - 1:
                continue
            band = P[a:b, v - 1 : v + 2].any(axis=1)
        else:
            if v < 1 or v >= P.shape[0] - 1:
                continue
            band = P[v - 1 : v + 2, a:b].any(axis=0)
        if band.size and band.mean() >= thr:
            return v
    return None


def find_rect(P: np.ndarray, face: tuple, w: int, h: int) -> tuple[list[int], str]:
    fx, fy, fw, fh = face
    ys = (int(fy - 0.6 * fh), int(min(h, fy + 1.6 * fh)))
    xs = (int(fx - 0.6 * fw), int(min(w, fx + 1.6 * fw)))
    left = _find_edge(P, "x", range(int(fx - 0.25 * fw), int(max(0, fx - 4 * fw)), -1), ys)
    right = _find_edge(P, "x", range(int(fx + 1.25 * fw), int(min(w, fx + 5 * fw))), ys)
    top = _find_edge(P, "y", range(int(fy - 0.3 * fh), int(max(0, fy - 3 * fh)), -1), xs)
    bottom = _find_edge(P, "y", range(int(fy + 1.4 * fh), int(min(h, fy + 4 * fh))), xs)
    found = sum(v is not None for v in (left, right, top, bottom))
    cx = fx + fw / 2
    # valeurs de repli : webcam 16:9 d'environ 3 largeurs de visage
    est_w = fw * 3.2
    if left is not None and right is not None:
        est_w = right - left
    est_h = est_w * 9 / 16
    if top is not None and bottom is not None:
        est_h = bottom - top
    if left is None:
        left = right - est_w if right is not None else cx - est_w / 2
        if left < 1.2 * fw:  # visage proche du bord : la webcam touche le bord
            left = 0
    if right is None:
        right = left + est_w
        if w - right < 1.2 * fw:
            right = w
    if top is None:
        top = bottom - est_h if bottom is not None else fy + fh * 0.45 - est_h * 0.42
        if top < 1.2 * fh:
            top = 0
    if bottom is None:
        bottom = top + est_h
        if h - bottom < 1.2 * fh:
            bottom = h
    left, right = max(0, left), min(w, right)
    top, bottom = max(0, top), min(h, bottom)
    method = "contours" if found >= 3 else ("contours partiels" if found else "estimation")
    return [int(left), int(top), int(right - left), int(bottom - top)], method


def detect_facecam(ctx: Context, src: Path, info: dict, n_frames: int = 24, pct_range=(0, 100)) -> dict:
    def compute() -> dict:
        import cv2

        lo, hi = pct_range
        dur = info["duration"]
        times = np.linspace(dur * 0.05, dur * 0.95, n_frames) if dur > 4 else [dur / 2]
        frames, faces = [], []
        det = None
        for i, t in enumerate(times):
            ctx.check_cancel()
            img = grab_frame(src, float(t))
            ctx.progress(lo + (hi - lo) * 0.8 * (i + 1) / len(times), "Recherche de la facecam")
            if img is None:
                continue
            if det is None:
                det = FaceDetector((img.shape[1], img.shape[0]))
            fi = len(frames)
            frames.append(img)
            for f in det.detect(img):
                faces.append((fi, f[:4]))
        if not frames:
            return {"found": False, "reason": "aucune image lisible"}
        h, w = frames[0].shape[:2]
        scale = info["width"] / w
        cv2.imwrite(str(ctx.path("frame_ref.jpg")), frames[len(frames) // 2], [cv2.IMWRITE_JPEG_QUALITY, 85])
        clusters = _cluster(faces, w, h)
        best, best_n = None, 0
        for c in clusters:
            n = len({fi for fi, _ in c})
            fw_med = float(np.median([f[2] for _, f in c]))
            if fw_med > 0.3 * w:  # visage plein écran : pas une incrustation
                continue
            if n > best_n:
                best, best_n = c, n
        need = max(3, int(0.3 * len(frames)))
        if not best or best_n < need:
            return {"found": False, "reason": "aucun visage fixe détecté", "frames": len(frames), "ref_w": w}
        face = tuple(float(np.median([f[k] for _, f in best])) for k in range(4))
        # contours persistants (présents sur 60 % des images)
        edges = [cv2.Canny(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), 60, 150) > 0 for fr in frames]
        P = np.mean(edges, axis=0) >= 0.6
        rect, method = find_rect(P, face, w, h)
        ctx.progress(hi, "Facecam analysée")
        to_src = lambda v: int(round(v * scale))
        rect_src = [to_src(v) for v in rect]
        face_src = [to_src(v) for v in face]
        ctx.log(f"Facecam détectée ({method}, visage présent sur {best_n}/{len(frames)} images)")
        return {
            "found": True,
            "rect": rect_src,
            "face": face_src,
            "confidence": round(best_n / len(frames), 2),
            "method": method,
            "frames": len(frames),
        }

    return cached_step(ctx, "facecam", {"v": 1}, compute)
