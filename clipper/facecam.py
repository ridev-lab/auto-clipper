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
MAX_CAM_FACE = 0.14   # largeur max d'un visage de webcam incrustée (part de l'image)
BIG_FACE = 0.12       # au-delà : visage « plein écran » → layout suivi


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
            # référence = médiane du groupe (robuste au premier visage mal placé)
            ref = [float(np.median([g[1][k] for g in c])) for k in range(4)]
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


def face_liveness(frames: list, face: tuple) -> float:
    """Variation moyenne (0-255) de la zone du visage au fil des images :
    quasi nulle pour une image fixe, nettement plus haute pour une webcam."""
    import cv2

    fx, fy, fw, fh = (int(round(v)) for v in face)
    crops = []
    for fr in frames:
        c = fr[max(0, fy) : fy + fh, max(0, fx) : fx + fw]
        if c.size == 0:
            continue
        crops.append(cv2.resize(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), (32, 32)).astype(np.float32))
    if len(crops) < 3:
        return 0.0
    return float(np.std(np.stack(crops), axis=0).mean())


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
    # valeurs de repli prudentes (mieux vaut zoomer un peu sur le visage
    # qu'inclure du gameplay dans la zone facecam)
    est_w = fw * 2.4
    if left is not None and right is not None:
        est_w = right - left
    est_h = fh * 1.6
    if top is not None and bottom is not None:
        est_h = bottom - top
    if left is None:
        if right is not None:
            left = right - est_w
        elif fx < 1.2 * fw:  # visage proche du bord : la webcam touche le bord
            left = 0
        else:
            left = cx - est_w / 2
    if right is None:
        right = left + est_w
        if w - right < 0.5 * fw:
            right = w
    if top is None:
        if bottom is not None:
            top = bottom - est_h
        elif fy < 1.0 * fh:
            top = 0
        else:
            top = fy + fh * 0.45 - est_h * 0.45
    if bottom is None:
        bottom = top + est_h
        if h - bottom < 0.5 * fh:
            bottom = h
    left, right = max(0, left), min(w, right)
    top, bottom = max(0, top), min(h, bottom)
    method = "contours" if found >= 3 else ("contours partiels" if found else "estimation")
    return [int(left), int(top), int(right - left), int(bottom - top)], method


def auto_layout_for_clip(src: Path, info: dict, facecam: dict | None, start: float, end: float, samples: int = 6) -> str:
    """Choix du layout pour un clip en mode auto :
    - facecam : la webcam incrustée est visible pendant le clip ;
    - track   : un grand visage occupe l'écran (webcam plein écran, IRL) ;
    - blur    : sinon (gameplay)."""
    scale = WORK_W / info["width"]
    rect = facecam.get("rect") if facecam and facecam.get("found") else None
    det = None
    in_cam = big = n = 0
    for t in np.linspace(start + 0.5, max(start + 0.6, end - 0.5), samples):
        img = grab_frame(src, float(t))
        if img is None:
            continue
        n += 1
        if det is None:
            det = FaceDetector((img.shape[1], img.shape[0]), threshold=0.6)
        faces = det.detect(img)
        if rect:
            x, y, w, h = (v * scale for v in rect)
            # zone facecam élargie de 20 % pour tolérer les mouvements
            x0, y0, x1, y1 = x - 0.2 * w, y - 0.2 * h, x + 1.2 * w, y + 1.2 * h
            cam_max = MAX_CAM_FACE * img.shape[1]
            if any(x0 <= f[0] + f[2] / 2 <= x1 and y0 <= f[1] + f[3] / 2 <= y1 and f[2] <= cam_max for f in faces):
                in_cam += 1
        if any(f[2] >= BIG_FACE * img.shape[1] for f in faces):
            big += 1
    if not n:
        return "facecam" if rect else "blur"
    if rect and in_cam >= max(2, np.ceil(0.4 * n)):
        return "facecam"
    if big >= np.ceil(0.5 * n):
        return "track"
    return "blur"


def detect_facecam(ctx: Context, src: Path, info: dict, n_frames: int = 30, pct_range=(0, 100)) -> dict:
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
            if fw_med > MAX_CAM_FACE * w:  # gros visage : webcam plein écran, pas une incrustation
                continue
            if fw_med < 0.035 * w:  # trop petit : vignette, affiche, PNJ lointain
                continue
            cx = float(np.median([f[0] + f[2] / 2 for _, f in c])) / w
            cy = float(np.median([f[1] + f[3] / 2 for _, f in c])) / h
            if 0.38 < cx < 0.62 and 0.25 < cy < 0.75:  # plein centre : rarement une incrustation
                continue
            if n > best_n:
                best, best_n = c, n
        # la facecam peut disparaître (scènes plein écran) : 30 % des images suffisent,
        # les vignettes fixes sont écartées par le test de mouvement ci-dessous
        need = max(4, int(np.ceil(0.3 * len(frames))))
        if not best or best_n < need:
            return {"found": False, "reason": "aucun visage fixe détecté", "frames": len(frames), "ref_w": w}
        face = tuple(float(np.median([f[k] for _, f in best])) for k in range(4))
        live = face_liveness(frames, face)
        if live < 4.0:
            # visage immobile d'une image à l'autre : vignette, affiche ou avatar figé
            ctx.log(f"Visage fixe ignoré : image statique (variation {live:.1f})")
            return {"found": False, "reason": "visage statique (image, pas une webcam)", "frames": len(frames), "liveness": round(live, 2)}
        # contours persistants, calculés sur les images où la webcam est visible
        with_cam = sorted({fi for fi, _ in best})
        edges = [cv2.Canny(cv2.cvtColor(frames[i], cv2.COLOR_BGR2GRAY), 60, 150) > 0 for i in with_cam]
        E = np.mean(edges, axis=0)
        rect, method = find_rect(E >= 0.6, face, w, h)
        if method == "estimation":
            rect, method = find_rect(E >= 0.4, face, w, h)
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

    return cached_step(ctx, "facecam", {"v": 5}, compute)
