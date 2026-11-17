"""Recadrage 9:16. La géométrie (rectangles source → destination) est
calculée ici une seule fois et sert à la fois au filtre ffmpeg et à
l'aperçu dans le navigateur : le rendu final correspond à l'aperçu."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .config import ffmpeg, get_config
from .utils import CREATE_NO_WINDOW, Context

LAYOUTS = ("auto", "facecam", "blur", "track")


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def out_size() -> tuple[int, int]:
    r = get_config()["reframe"]
    return int(r["output_width"]), int(r["output_height"])


def resolve_layout(layout: str, facecam: dict | None) -> str:
    if layout == "facecam" and not (facecam and facecam.get("rect")):
        return "blur"
    if layout == "auto":
        return "facecam" if facecam and facecam.get("found") and facecam.get("rect") else "blur"
    return layout if layout in LAYOUTS else "blur"


def _crop_to_aspect(rect: list[float], aspect: float, center_x: float | None = None) -> list[int]:
    """Plus grand rectangle d'aspect `aspect` (l/h) contenu dans `rect`."""
    x, y, w, h = rect
    if w / h > aspect:
        nw = h * aspect
        cx = center_x if center_x is not None else x + w / 2
        nx = min(max(x, cx - nw / 2), x + w - nw)
        return [_even(nx) if nx > 1 else 0, int(y), _even(nw), _even(h)]
    nh = w / aspect
    ny = y + (h - nh) / 2
    return [int(x), _even(ny) if ny > 1 else 0, _even(w), _even(nh)]


def _fix(rect: list[int], src_w: int, src_h: int) -> list[int]:
    x, y, w, h = rect
    w, h = min(w, src_w - (src_w % 2)), min(h, src_h - (src_h % 2))
    x = int(max(0, min(x, src_w - w)))
    y = int(max(0, min(y, src_h - h)))
    return [x, y, int(w), int(h)]


def layout_geometry(layout: str, src_w: int, src_h: int, facecam: dict | None = None) -> dict:
    W, H = out_size()
    rcfg = get_config()["reframe"]
    layout = resolve_layout(layout, facecam)
    src_aspect = src_w / src_h
    layers: list[dict] = []
    hook_y = int(H * 0.17)
    split_y = None

    if layout == "facecam":
        top_h = _even(H * float(rcfg["facecam_height_ratio"]))
        bot_h = H - top_h
        face = facecam.get("face") if facecam else None
        fcx = face[0] + face[2] / 2 if face else None
        fc = _crop_to_aspect([float(v) for v in facecam["rect"]], W / top_h, fcx)
        gp = _crop_to_aspect([0, 0, src_w, src_h], W / bot_h)
        layers = [
            {"src": _fix(fc, src_w, src_h), "dst": [0, 0, W, top_h], "blur": False},
            {"src": _fix(gp, src_w, src_h), "dst": [0, top_h, W, bot_h], "blur": False},
        ]
        split_y = top_h
        hook_y = top_h
    elif layout == "track":
        cw = _even(min(src_w, src_h * W / H))
        layers = [{"src": [_even((src_w - cw) / 2), 0, cw, _even(src_h) if src_h % 2 == 0 else src_h - 1], "dst": [0, 0, W, H], "blur": False, "track": True}]
    else:  # blur
        if src_aspect <= W / H + 0.02:  # déjà vertical : simple recadrage
            layers = [{"src": _fix(_crop_to_aspect([0, 0, src_w, src_h], W / H), src_w, src_h), "dst": [0, 0, W, H], "blur": False}]
        else:
            zoom = max(1.0, min(2.0, float(rcfg.get("blur_zoom", 1.0))))
            crop_w = _even(src_w / zoom)
            fg_h = _even(W * src_h / crop_w)
            bg = _crop_to_aspect([0, 0, src_w, src_h], W / H)
            fy = _even((H - fg_h) / 2)
            layers = [
                {"src": _fix(bg, src_w, src_h), "dst": [0, 0, W, H], "blur": True},
                {"src": _fix([_even((src_w - crop_w) / 2), 0, crop_w, src_h - src_h % 2], src_w, src_h), "dst": [0, fy, W, fg_h], "blur": False},
            ]
            hook_y = int(fy / 2)
    return {
        "layout": layout,
        "width": W,
        "height": H,
        "src_w": src_w,
        "src_h": src_h,
        "layers": layers,
        "split_y": split_y,
        "hook_y": hook_y,
        "blur_sigma": float(rcfg["blur_sigma"]),
    }


def build_filtergraph(geom: dict, fps: float, duration: float, ass_file: str | None, sendcmd_file: str | None) -> str:
    W, H = geom["width"], geom["height"]
    layers = geom["layers"]
    k = len(layers)
    parts: list[str] = []
    if k > 1:
        parts.append("[0:v]split=%d%s" % (k, "".join(f"[s{i}]" for i in range(k))))
    for i, L in enumerate(layers):
        x, y, w, h = L["src"]
        dx, dy, dw, dh = L["dst"]
        inp = f"[s{i}]" if k > 1 else "[0:v]"
        if L.get("track") and sendcmd_file:
            chain = f"{inp}sendcmd=f={sendcmd_file},crop@trk=w={w}:h={h}:x={x}:y={y}"
        else:
            chain = f"{inp}crop={w}:{h}:{x}:{y}"
        if L.get("blur"):
            sw, sh = _even(dw / 4), _even(dh / 4)
            sigma = max(1.0, geom["blur_sigma"] / 4 * 2)
            chain += f",scale={sw}:{sh}:flags=bilinear,gblur=sigma={sigma:.1f},scale={dw}:{dh}:flags=bicubic,eq=brightness=-0.06"
        else:
            chain += f",scale={dw}:{dh}:flags=bicubic"
        chain += f",setsar=1[l{i}]"
        parts.append(chain)
    parts.append(f"color=c=black:s={W}x{H}:r={fps:.3f}:d={duration + 0.5:.3f}[base]")
    prev = "base"
    for i, L in enumerate(layers):
        dx, dy = L["dst"][0], L["dst"][1]
        parts.append(f"[{prev}][l{i}]overlay={dx}:{dy}:shortest=1[o{i}]")
        prev = f"o{i}"
    tail = f"[{prev}]"
    tail += f"ass=filename={ass_file}:fontsdir=fonts," if ass_file else ""
    tail += "format=yuv420p[vout]"
    parts.append(tail)
    return ";".join(parts)


# --------------------------------------------------------------------------
# Layout "track" : trajectoire lissée du centre d'intérêt
# --------------------------------------------------------------------------

def _smooth(values: np.ndarray, alpha: float) -> np.ndarray:
    """Lissage exponentiel aller-retour (sans retard)."""
    if values.size == 0:
        return values
    out = values.astype(np.float64).copy()
    for rng in (range(1, out.size), range(out.size - 2, -1, -1)):
        for i in rng:
            j = i - 1 if rng.step == 1 else i + 1
            out[i] = alpha * out[j] + (1 - alpha) * out[i]
    return out


def compute_track(ctx: Context, src: Path, info: dict, start: float, end: float, motion: dict | None) -> list[list[float]]:
    """Retourne [[t_relatif, x_crop], ...] à 4 points par seconde."""
    import cv2

    from .facecam import FaceDetector

    src_w, src_h = info["width"], info["height"]
    W, H = out_size()
    cw = _even(min(src_w, src_h * W / H))
    dur = max(0.1, end - start)
    fps_s, work_w = 4, 640
    work_h = _even(work_w * src_h / src_w)
    cmd = [
        ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
        "-vf", f"fps={fps_s},scale={work_w}:{work_h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    det = FaceDetector((work_w, work_h), threshold=0.75)
    frame_size = work_w * work_h * 3
    centers: list[float | None] = []
    prev_cx = None
    while True:
        ctx.check_cancel()
        buf = proc.stdout.read(frame_size)
        if len(buf) < frame_size:
            break
        img = np.frombuffer(buf, np.uint8).reshape(work_h, work_w, 3)
        faces = det.detect(img)
        cx = None
        if faces:
            # visage le plus grand, ou le plus proche de la position précédente
            faces.sort(key=lambda f: f[2] * f[3], reverse=True)
            cand = faces[:3]
            if prev_cx is not None:
                cand.sort(key=lambda f: abs((f[0] + f[2] / 2) / work_w - prev_cx))
            f = cand[0]
            cx = (f[0] + f[2] / 2) / work_w
        elif motion and motion.get("cx"):
            t_abs = start + len(centers) / fps_s
            i = int(t_abs / motion.get("hop", 0.5))
            vals = [v for v in motion["cx"][max(0, i - 2) : i + 3] if v is not None]
            cx = float(np.median(vals)) if vals else None
        centers.append(cx)
        if cx is not None:
            prev_cx = cx
    proc.stdout.close()
    proc.wait()
    if not centers:
        return [[0.0, float(_even((src_w - cw) / 2))]]
    # trous comblés par la dernière valeur connue (ou le centre)
    filled, last = [], 0.5
    first_known = next((c for c in centers if c is not None), 0.5)
    last = first_known
    for c in centers:
        last = c if c is not None else last
        filled.append(last)
    arr = np.array(filled)
    # zone morte : on ne bouge que si la cible s'éloigne de plus de 6 %
    held = arr.copy()
    for i in range(1, held.size):
        held[i] = held[i - 1] if abs(arr[i] - held[i - 1]) < 0.06 else arr[i]
    alpha = float(get_config()["reframe"]["track_smoothing"])
    sm = _smooth(held, alpha)
    xs = np.clip(sm * src_w - cw / 2, 0, src_w - cw)
    return [[round(i / fps_s, 3), float(round(x, 1))] for i, x in enumerate(xs)]


def write_sendcmd(track: list[list[float]], path: Path, step: float = 0.1) -> None:
    if not track:
        path.write_text("", encoding="utf-8")
        return
    ts = np.array([p[0] for p in track])
    xs = np.array([p[1] for p in track])
    end = float(ts[-1]) + 1.0
    lines = []
    for t in np.arange(0, end, step):
        x = float(np.interp(t, ts, xs))
        lines.append(f"{t:.2f} crop@trk x {int(round(x))};")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
