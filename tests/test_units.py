"""Tests unitaires des fonctions pures (rapides, sans GPU ni réseau)."""
import json

import numpy as np
import pytest

from clipper.highlights import (
    SignalModel, adjust_bounds, build_sentences, fit_duration, iou, llm_windows,
    percentile_rank, select_top, _fit_sentences,
)
from clipper.reframe import build_filtergraph, layout_geometry, resolve_layout, write_sendcmd
from clipper.subtitles import ass_color, ass_time, build_ass, build_groups, caption_payload, clip_words
from clipper.transcribe_worker import merge_tokens
from clipper.metadata import apply_keywords, norm_word


def W(t, s, e):
    return {"t": t, "s": s, "e": e}


WORDS = [
    W("Salut", 0.0, 0.4), W("tout", 0.5, 0.7), W("le", 0.7, 0.8), W("monde.", 0.8, 1.2),
    W("Regarde", 2.0, 2.4), W("cette", 2.4, 2.6), W("voiture", 2.6, 3.0), W("!", 3.0, 3.05),
    W("Elle", 5.0, 5.2), W("vole", 5.2, 5.6), W("littéralement.", 5.6, 6.4),
]


# --- fusion des mots Whisper -------------------------------------------------

def test_merge_tokens_french():
    raw = [W("c", 0, .1), W("'est", .1, .3), W("grand", .4, .6), W("-mère,", .6, .9), W("quoi", 1, 1.2), W("?", 1.2, 1.25), W("j'", 1.3, 1.4), W("ai", 1.4, 1.5)]
    out = merge_tokens(raw)
    assert [w["t"] for w in out] == ["c'est", "grand-mère,", "quoi ?", "j'ai"]
    assert out[0]["s"] == 0 and out[0]["e"] == .3


def test_merge_tokens_keeps_far_words():
    out = merge_tokens([W("bon", 0, .2), W("-", 2.0, 2.1)])
    assert [w["t"] for w in out] == ["bon -"]  # ponctuation seule toujours rattachée


# --- phrases et bornes --------------------------------------------------------

def test_build_sentences_splits_on_punctuation_and_pauses():
    s = build_sentences(WORDS)
    assert [x["text"] for x in s] == ["Salut tout le monde.", "Regarde cette voiture !", "Elle vole littéralement."]
    assert s[1]["s"] == 2.0 and s[1]["e"] == 3.05


def test_fit_duration_bounds():
    assert fit_duration(10, 12, 20, 60, 100) == (pytest.approx(10 - 18 * 0.4, abs=0.01), pytest.approx(10 - 18 * 0.4 + 20, abs=0.01))
    s, e = fit_duration(0, 200, 20, 60, 300, anchor=100)
    assert e - s == pytest.approx(60)
    assert s <= 100 <= e
    s, e = fit_duration(90, 95, 20, 60, 100)
    assert e <= 100 and e - s == pytest.approx(20)


def test_adjust_bounds_never_cuts_words():
    sents = build_sentences(WORDS)
    s, e = adjust_bounds(2.5, 5.3, WORDS, sents, 0.3, 2, 10, 10)
    # le début recule au début de la phrase, la fin va au bout de la phrase
    assert s <= 2.0 and s >= 1.2
    assert e >= 6.4
    for w in WORDS:
        assert not (w["s"] < s < w["e"]) and not (w["s"] < e < w["e"])


def test_fit_sentences_respects_duration():
    sents = build_sentences(WORDS)
    a, b = _fit_sentences(sents, 1, 1, 4, 10)
    assert sents[b]["e"] - sents[a]["s"] >= 4


def test_select_top_no_overlap():
    c = [{"start": 0, "end": 10, "score": 90}, {"start": 5, "end": 15, "score": 80}, {"start": 20, "end": 30, "score": 70}]
    out = select_top(c, 3, 1.0)
    assert [x["score"] for x in out] == [90, 70]
    assert iou(c[0], c[1]) == pytest.approx(5 / 15)


def test_percentile_and_signal_model():
    assert percentile_rank(5, np.array([1, 2, 3, 4])) == 1.0
    assert percentile_rank(0, np.array([1, 2, 3])) == 0.0
    excite = [0.0] * 200
    excite[100:110] = [1.0] * 10  # pic sonore à 50-55 s
    m = SignalModel(100, excite, [0.1] * 200, [52.0, 53.0], 20)
    hot = m.scores(45, 65)
    cold = m.scores(0, 20)
    assert hot["audio"] > cold["audio"]
    assert hot["scenes"] > cold["scenes"]


def test_llm_windows_overlap():
    sents = [{"id": i, "s": i * 10.0, "e": i * 10 + 9.0, "text": "x"} for i in range(100)]
    wins = llm_windows(sents, 360, 60)
    assert len(wins) >= 3
    assert wins[0][-1]["s"] >= wins[1][0]["s"]  # chevauchement


# --- sous-titres --------------------------------------------------------------

def test_ass_color_and_time():
    assert ass_color("#FFE600") == "&H0000E6FF"
    assert ass_color("#000000", 0x60) == "&H60000000"
    assert ass_time(3723.456) == "1:02:03.46"


PRESET = {
    "font_file": "Montserrat-Black.ttf", "font_size": 96, "uppercase": True, "color": "#FFFFFF",
    "active_color": "#FFE600", "keyword_colors": ["#FFE600"], "outline_color": "#000000",
    "outline": 7, "shadow": 2, "max_words": 3, "active_scale": 108, "y": 0.66,
}


def test_build_groups_limits_words_and_width():
    words = [{"s": i * 0.3, "e": i * 0.3 + 0.25, "t": t, "kw": False} for i, t in enumerate("un deux trois quatre cinq anticonstitutionnellement".split())]
    groups = build_groups(words, PRESET)
    assert all(len(g["words"]) <= 3 for g in groups)
    assert groups[-1]["scale"] <= 1.0
    for a, b in zip(groups, groups[1:]):
        assert a["e"] <= b["s"] + 1e-6


def test_build_ass_events():
    geom = layout_geometry("blur", 1920, 1080)
    words = clip_words([{"s": 10.0, "e": 10.4, "t": "Salut", "kw": True}, {"s": 10.5, "e": 10.9, "t": "toi"}], 10.0, 20.0)
    payload = caption_payload(words, "classique", geom, "Mon hook", True, 10.0)
    ass = build_ass(payload, 1080, 1920)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert ass.count("Dialogue: 1,") == 2
    assert "Mon hook" in ass and "Dialogue: 2," in ass
    assert "SALUT" in ass


# --- recadrage ----------------------------------------------------------------

def test_layout_geometry_blur_and_facecam():
    g = layout_geometry("blur", 1920, 1080)
    assert g["layout"] == "blur" and len(g["layers"]) == 2
    fg = g["layers"][1]
    assert fg["dst"][2] == 1080 and fg["dst"][3] % 2 == 0
    fc = {"found": True, "rect": [1500, 40, 380, 214], "face": [1640, 90, 90, 100]}
    g2 = layout_geometry("facecam", 1920, 1080, fc)
    top, bottom = g2["layers"]
    assert top["dst"] == [0, 0, 1080, 672]
    x, y, w, h = top["src"]
    assert 1500 <= x and x + w <= 1880 and w / h == pytest.approx(1080 / 672, rel=0.03)
    assert bottom["dst"][1] == 672 and bottom["dst"][3] == 1920 - 672
    assert resolve_layout("auto", None) == "blur"
    assert resolve_layout("auto", fc) == "facecam"
    assert resolve_layout("facecam", None) == "blur"


def test_filtergraph_contains_all_parts(tmp_path):
    g = layout_geometry("blur", 1920, 1080)
    fg = build_filtergraph(g, 60, 30, "clip.ass", None)
    assert "gblur" in fg and "overlay" in fg and "ass=filename=clip.ass" in fg and fg.endswith("[vout]")
    gt = layout_geometry("track", 1920, 1080)
    ft = build_filtergraph(gt, 30, 10, None, "track.cmd")
    assert "sendcmd=f=track.cmd" in ft and "crop@trk" in ft
    p = tmp_path / "t.cmd"
    write_sendcmd([[0, 100.0], [1, 200.0]], p)
    lines = p.read_text().splitlines()
    assert lines[0] == "0.00 crop@trk x 100;" and "150" in lines[5]


def test_face_liveness_rejects_static_images():
    from clipper.facecam import face_liveness

    rng = np.random.default_rng(0)
    static = rng.integers(0, 255, (90, 160, 3), dtype=np.uint8)
    frames_static = [static.copy() for _ in range(8)]
    frames_live = [rng.integers(0, 255, (90, 160, 3), dtype=np.uint8) for _ in range(8)]
    face = (40, 20, 30, 30)
    assert face_liveness(frames_static, face) < 1.0
    assert face_liveness(frames_live, face) > 20.0


# --- métadonnées -------------------------------------------------------------

def test_keywords_marking():
    words = [{"t": "Incroyable!"}, {"t": "la"}, {"t": "Voiture"}]
    apply_keywords(words, ["incroyable", "voiture rouge"])
    assert [bool(w.get("kw")) for w in words] == [True, False, True]
    assert norm_word("Élégant,") == "elegant"
