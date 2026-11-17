"""Sous-titres karaoké mot par mot au format ASS (libass).

Le découpage en lignes est calculé ici (mesure réelle du texte avec la
police) et renvoyé tel quel à l'aperçu du navigateur."""
from __future__ import annotations

import re
import struct
from functools import lru_cache
from pathlib import Path

from .config import FONTS_DIR, get_config

SAFE_X_CENTER = 520   # légèrement à gauche : boutons TikTok/Reels à droite
SAFE_MAX_WIDTH = 800
HOOK_MAX_WIDTH = 900
STRONG_END = re.compile(r"[.!?…]$")


# --------------------------------------------------------------------------
# Polices
# --------------------------------------------------------------------------

@lru_cache(maxsize=None)
def ttf_names(path: str) -> dict[int, str]:
    """Lit la table 'name' d'une police TrueType/OpenType."""
    data = Path(path).read_bytes()
    num_tables = struct.unpack(">H", data[4:6])[0]
    off = None
    for i in range(num_tables):
        rec = data[12 + 16 * i : 28 + 16 * i]
        if rec[:4] == b"name":
            off = struct.unpack(">I", rec[8:12])[0]
            break
    if off is None:
        return {}
    _, count, str_off = struct.unpack(">HHH", data[off : off + 6])
    names: dict[int, str] = {}
    for i in range(count):
        pid, eid, lid, nid, length, noff = struct.unpack(">HHHHHH", data[off + 6 + 12 * i : off + 18 + 12 * i])
        raw = data[off + str_off + noff : off + str_off + noff + length]
        if pid == 3 and lid in (0x409, 0x40C):
            names.setdefault(nid, raw.decode("utf-16-be", "replace"))
        elif pid == 1 and nid not in names:
            names[nid] = raw.decode("latin-1", "replace")
    return names


def font_file(name: str) -> Path:
    p = FONTS_DIR / name
    if not p.exists():
        cands = sorted(FONTS_DIR.glob("*.ttf"))
        if not cands:
            raise FileNotFoundError("Aucune police dans fonts/ : relance setup.bat")
        p = cands[0]
    return p


def font_family(name: str) -> str:
    n = ttf_names(str(font_file(name)))
    return n.get(1) or n.get(4) or Path(name).stem


@lru_cache(maxsize=64)
def _pil_font(name: str, size: int):
    from PIL import ImageFont

    return ImageFont.truetype(str(font_file(name)), size)


def text_width(text: str, font: str, size: int, outline: float = 0) -> float:
    return _pil_font(font, int(size)).getlength(text) + 2 * outline


# --------------------------------------------------------------------------
# Presets
# --------------------------------------------------------------------------

def get_preset(name: str | None) -> tuple[str, dict]:
    presets = get_config()["subtitle_presets"]
    if name not in presets:
        name = get_config()["defaults"].get("subtitle_preset")
    if name not in presets:
        name = next(iter(presets))
    return name, presets[name]


def presets_public() -> list[dict]:
    out = []
    for key, p in get_config()["subtitle_presets"].items():
        out.append({"id": key, **p, "font_family": font_family(p["font_file"])})
    return out


# --------------------------------------------------------------------------
# Découpage
# --------------------------------------------------------------------------

def display_text(t: str, preset: dict) -> str:
    return t.upper() if preset.get("uppercase") else t


def build_groups(words: list[dict], preset: dict) -> list[dict]:
    """words : [{s, e, t, kw}] en secondes relatives au début du clip.
    Retourne des groupes d'une ligne : {s, e, words, scale}."""
    size = int(preset["font_size"])
    font = preset["font_file"]
    outline = float(preset.get("outline", 0))
    max_words = int(preset.get("max_words", 3))
    groups: list[dict] = []
    cur: list[dict] = []

    def width(ws):
        return text_width(" ".join(display_text(w["t"], preset) for w in ws), font, size, outline)

    def flush():
        if cur:
            groups.append({"words": list(cur)})
            cur.clear()

    for i, w in enumerate(words):
        if cur:
            gap = w["s"] - cur[-1]["e"]
            if len(cur) >= max_words or gap > 0.6 or STRONG_END.search(cur[-1]["t"]) or width(cur + [w]) > SAFE_MAX_WIDTH:
                flush()
        cur.append(w)
    flush()
    for gi, g in enumerate(groups):
        ws = g["words"]
        g["s"] = ws[0]["s"]
        nxt = groups[gi + 1]["words"][0]["s"] if gi + 1 < len(groups) else None
        end = ws[-1]["e"] + 0.35
        g["e"] = round(min(end, nxt) if nxt is not None else end, 3)
        wdt = width(ws)
        g["scale"] = round(min(1.0, SAFE_MAX_WIDTH / wdt), 3) if wdt > 0 else 1.0
    return groups


def wrap_hook(text: str, font: str, size: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        test = f"{cur} {w}".strip()
        if cur and text_width(test, font, size) > HOOK_MAX_WIDTH - 60:
            lines.append(cur)
            cur = w
        else:
            cur = test
    if cur:
        lines.append(cur)
    if len(lines) > 2:
        lines = [lines[0], " ".join(lines[1:])]
    return lines


# --------------------------------------------------------------------------
# ASS
# --------------------------------------------------------------------------

def ass_color(hex_color: str, alpha: int = 0) -> str:
    h = (hex_color or "#FFFFFF").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _esc(t: str) -> str:
    return t.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def caption_payload(words: list[dict], preset_name: str | None, geom: dict, hook: str, show_hook: bool, duration: float) -> dict:
    """Tout ce qu'il faut pour dessiner les sous-titres (ASS et aperçu)."""
    pname, preset = get_preset(preset_name)
    hcfg = get_config()["hook"]
    H = geom["height"]
    groups = build_groups(words, preset)
    hook_lines = wrap_hook(hook.strip(), hcfg["font_file"], int(hcfg["font_size"])) if (show_hook and hook and hook.strip()) else []
    return {
        "preset_id": pname,
        "preset": {**preset, "font_family": font_family(preset["font_file"])},
        "x": SAFE_X_CENTER,
        "y": int(H * float(preset.get("y", 0.66))),
        "groups": groups,
        "hook": {
            "lines": hook_lines,
            "y": geom["hook_y"],
            "duration": min(float(hcfg["duration"]), duration),
            "font_file": hcfg["font_file"],
            "font_family": font_family(hcfg["font_file"]),
            "font_size": int(hcfg["font_size"]),
            "color": hcfg["color"],
            "box_color": hcfg["box_color"],
        },
    }


def build_ass(payload: dict, width: int, height: int) -> str:
    p = payload["preset"]
    hk = payload["hook"]
    size = int(p["font_size"])
    kw_colors = p.get("keyword_colors") or [p["active_color"]]
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{p['font_family']},{size},{ass_color(p['color'])},{ass_color(p['active_color'])},"
        f"{ass_color(p['outline_color'])},{ass_color('#000000', 0x60)},0,0,0,0,100,100,0,0,1,"
        f"{float(p.get('outline', 6)):.1f},{float(p.get('shadow', 0)):.1f},5,0,0,0,1",
        f"Style: Hook,{hk['font_family']},{hk['font_size']},{ass_color(hk['color'])},{ass_color(hk['color'])},"
        f"{ass_color(hk['box_color'])},{ass_color('#000000', 0xFF)},0,0,0,0,100,100,0,0,3,16,0,5,0,0,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    x, y = payload["x"], payload["y"]
    active_scale = int(p.get("active_scale", 100))
    for g in payload["groups"]:
        ws = g["words"]
        scale_tag = "" if g["scale"] >= 0.999 else f"\\fscx{int(g['scale'] * 100)}\\fscy{int(g['scale'] * 100)}"
        base_scale = int(g["scale"] * 100)
        for k, w in enumerate(ws):
            start = w["s"] if k else g["s"]
            end = ws[k + 1]["s"] if k + 1 < len(ws) else g["e"]
            if end - start < 0.02:
                continue
            parts = []
            for j, ww in enumerate(ws):
                txt = _esc(display_text(ww["t"], p))
                if j == k:
                    col = p["active_color"]
                    big = int(base_scale * active_scale / 100)
                    pop = f"\\t(0,90,\\fscx{big}\\fscy{big})" if active_scale != 100 else ""
                    parts.append(f"{{\\1c{ass_color(col)}{pop}}}{txt}{{\\fscx{base_scale}\\fscy{base_scale}}}")
                else:
                    col = kw_colors[0] if ww.get("kw") else p["color"]
                    parts.append(f"{{\\1c{ass_color(col)}}}{txt}")
            text = f"{{\\pos({x},{y}){scale_tag}}}" + " ".join(parts)
            lines.append(f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Cap,,0,0,0,,{text}")
    if hk["lines"]:
        text = "\\N".join(_esc(t) for t in hk["lines"])
        lines.append(
            f"Dialogue: 2,{ass_time(0)},{ass_time(hk['duration'])},Hook,,0,0,0,,"
            f"{{\\pos({width // 2},{hk['y']})\\fad(120,250)}}{text}"
        )
    return "\n".join(lines) + "\n"


def clip_words(words_abs: list[dict], start: float, end: float) -> list[dict]:
    """Mots du clip (non supprimés), en temps relatifs au début du clip."""
    out = []
    for w in words_abs:
        if w.get("del"):
            continue
        s, e = w["s"] - start, w["e"] - start
        if e <= 0 or s >= end - start:
            continue
        t = (w.get("t") or "").strip()
        if not t:
            continue
        out.append({"s": round(max(0.0, s), 3), "e": round(min(end - start, e), 3), "t": t, "kw": bool(w.get("kw"))})
    return out
