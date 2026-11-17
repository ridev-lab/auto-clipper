"""Titre, description, hashtags (FR) et mots-clés à colorer, par le LLM."""
from __future__ import annotations

import re
import unicodedata

from . import llm
from .utils import ClipperError, Context

SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hook", "title", "description", "hashtags", "keywords"],
}

SYSTEM = """Tu es community manager expert TikTok, YouTube Shorts et Instagram Reels pour une chaîne gaming francophone.
Tu écris en français naturel, percutant, sans fautes, sans emoji. Réponds uniquement avec du JSON valide."""


def norm_word(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9']", "", t)


def fallback(clip: dict, source_title: str) -> dict:
    hook = (clip.get("hook") or "").strip() or (source_title[:50] if source_title else "Moment fou en jeu")
    return {
        "hook": hook,
        "title": hook,
        "description": f"Extrait de « {source_title} »." if source_title else "Le meilleur moment de la session.",
        "hashtags": ["#GTA6", "#gaming", "#gta", "#fyp"],
        "keywords": [],
    }


def generate(ctx: Context, clip: dict, excerpt: str, source_title: str, hint: str) -> dict:
    sig = clip.get("signals") or {}
    mood = []
    if sig.get("audio", 0) > 0.7:
        mood.append("gros pics sonores (cris, explosions)")
    if sig.get("motion", 0) > 0.7:
        mood.append("beaucoup d'action à l'écran")
    user = (
        f"Contexte : {hint}\n"
        f"Titre de la vidéo source : {source_title or 'inconnu'}\n"
        f"Durée du clip : {clip['end'] - clip['start']:.0f} s\n"
        + (f"Accroche proposée : {clip['hook']}\n" if clip.get("hook") else "")
        + (f"Ambiance détectée : {', '.join(mood)}\n" if mood else "")
        + ("Transcription du clip :\n" + excerpt[:2500] + "\n" if excerpt.strip() else "Pas de paroles : clip de pur gameplay.\n")
        + "\nGénère :\n"
        "- hook : texte affiché à l'écran pendant les 3 premières secondes, 3 à 7 mots, intrigant (reprends l'accroche proposée si elle est bonne) ;\n"
        "- title : titre YouTube Shorts, 90 caractères maximum ;\n"
        "- description : 1 à 2 phrases + un appel à l'action, sans hashtag ;\n"
        "- hashtags : 3 à 5 hashtags pertinents commençant par # ;\n"
        "- keywords : 2 à 5 mots EXACTS de la transcription à mettre en couleur (les plus forts), liste vide s'il n'y a pas de paroles."
    )
    try:
        res = llm.chat_json(SYSTEM, user, SCHEMA, temperature=0.5)
    except ClipperError as e:
        ctx.warn(f"Métadonnées par défaut (LLM indisponible : {e})")
        return fallback(clip, source_title)
    fb = fallback(clip, source_title)
    tags = []
    for h in res.get("hashtags") or []:
        h = "#" + re.sub(r"[^\w]", "", str(h).lstrip("#"))
        if len(h) > 1 and h.lower() not in [t.lower() for t in tags]:
            tags.append(h)
    return {
        "hook": (str(res.get("hook") or "").strip().strip('"') or fb["hook"])[:80],
        "title": (str(res.get("title") or "").strip().strip('"') or fb["title"])[:100],
        "description": (str(res.get("description") or "").strip() or fb["description"])[:2000],
        "hashtags": tags[:5] or fb["hashtags"],
        "keywords": [str(k) for k in (res.get("keywords") or [])][:6],
    }


def apply_keywords(words: list[dict], keywords: list[str]) -> None:
    keys = {norm_word(k) for kw in keywords for k in str(kw).split() if len(norm_word(k)) >= 3}
    for w in words:
        if norm_word(w.get("t", "")) in keys:
            w["kw"] = True
