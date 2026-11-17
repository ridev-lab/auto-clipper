// Timeline : vignettes, énergie audio, mots et coupes ; sélection
// début/fin avec poignées (accrochage aux limites de mots).
import { fmtTime, h } from "../ui.js";

const HEIGHT = 96;
const THUMB_H = 54;

export class Timeline {
  constructor({ data, start, end, window: win, onChange, onSeek, minLen = 1, maxLen = 180 }) {
    this.data = data;
    this.duration = data.duration || 1;
    this.s = start;
    this.e = end;
    this.onChange = onChange;
    this.onSeek = onSeek;
    this.minLen = minLen;
    this.maxLen = maxLen;
    this.play = null;
    this.canvas = h("canvas", { class: "tl-canvas", height: HEIGHT });
    this.sel = h("div", { class: "tl-sel", title: "Glisse pour déplacer le clip" },
      h("div", { class: "tl-handle l", title: "Début" }), h("div", { class: "tl-handle r", title: "Fin" }));
    this.playEl = h("div", { class: "tl-play", hidden: true });
    this.scale = h("div", { class: "tl-scale" });
    this.wrap = h("div", { class: "timeline" }, this.canvas, this.sel, this.playEl);
    this.el = h("div", {}, this.wrap, this.scale);
    this.sprite = null;
    if (data.sprite && data.sprite.url) {
      const img = new Image();
      img.onload = () => { this.sprite = img; this.draw(); };
      img.src = data.sprite.url;
    }
    this.wordBounds = (data.words || []).flatMap((w) => [w[0], w[1]]);
    this.setWindow(...(win || this.autoWindow()));
    this.bind();
    this.ro = new ResizeObserver(() => this.draw());
    this.ro.observe(this.wrap);
  }

  autoWindow(padFactor = 0.6) {
    const len = this.e - this.s;
    const pad = Math.max(12, len * padFactor);
    return [Math.max(0, this.s - pad), Math.min(this.duration, this.e + pad)];
  }

  setWindow(w0, w1) {
    w0 = Math.max(0, w0);
    w1 = Math.min(this.duration, Math.max(w0 + 2, w1));
    this.w0 = w0;
    this.w1 = w1;
    this.draw();
  }

  zoom(factor) {
    const c = (this.s + this.e) / 2;
    const half = ((this.w1 - this.w0) * factor) / 2;
    let w0 = c - half;
    let w1 = c + half;
    if (w0 < 0) { w1 -= w0; w0 = 0; }
    if (w1 > this.duration) { w0 -= w1 - this.duration; w1 = this.duration; }
    this.setWindow(w0, w1);
  }

  setRange(s, e) { this.s = s; this.e = e; this.position(); }
  setPlayhead(t) { this.play = t; this.position(); }

  x2t(x) {
    const w = this.wrap.clientWidth || 1;
    return this.w0 + (Math.max(0, Math.min(w, x)) / w) * (this.w1 - this.w0);
  }

  t2x(t) { return ((t - this.w0) / (this.w1 - this.w0)) * (this.wrap.clientWidth || 1); }

  snap(t) {
    const tol = (8 / (this.wrap.clientWidth || 1)) * (this.w1 - this.w0);
    let best = t;
    let bd = tol;
    for (const b of this.wordBounds) {
      const d = Math.abs(b - t);
      if (d < bd) { bd = d; best = b; }
    }
    return best;
  }

  bind() {
    const [hl, hr] = this.sel.children;
    let drag = null;
    const down = (kind) => (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      drag = { kind, x0: ev.clientX, s0: this.s, e0: this.e };
      ev.target.setPointerCapture(ev.pointerId);
    };
    hl.addEventListener("pointerdown", down("l"));
    hr.addEventListener("pointerdown", down("r"));
    this.sel.addEventListener("pointerdown", (ev) => { if (ev.target === this.sel) down("m")(ev); });
    const move = (ev) => {
      if (!drag) return;
      const dt = ((ev.clientX - drag.x0) / (this.wrap.clientWidth || 1)) * (this.w1 - this.w0);
      let { s0: s, e0: e } = drag;
      if (drag.kind === "l") s = this.snap(Math.min(e - this.minLen, Math.max(e - this.maxLen, s + dt)));
      else if (drag.kind === "r") e = this.snap(Math.max(s + this.minLen, Math.min(s + this.maxLen, e + dt)));
      else {
        const len = e - s;
        s = Math.max(0, Math.min(this.duration - len, s + dt));
        e = s + len;
      }
      s = Math.max(0, s);
      e = Math.min(this.duration, e);
      this.s = Math.round(s * 1000) / 1000;
      this.e = Math.round(e * 1000) / 1000;
      this.position();
      this.onChange && this.onChange(this.s, this.e, false);
    };
    const up = () => {
      if (!drag) return;
      drag = null;
      this.onChange && this.onChange(this.s, this.e, true);
    };
    [hl, hr, this.sel].forEach((el) => { el.addEventListener("pointermove", move); el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up); });
    this.canvas.addEventListener("pointerdown", (ev) => {
      const r = this.canvas.getBoundingClientRect();
      this.onSeek && this.onSeek(this.x2t(ev.clientX - r.left));
    });
    this.wrap.addEventListener("wheel", (ev) => {
      if (!ev.ctrlKey && !ev.altKey) return;
      ev.preventDefault();
      this.zoom(ev.deltaY > 0 ? 1.25 : 0.8);
    }, { passive: false });
  }

  position() {
    const x0 = this.t2x(this.s);
    const x1 = this.t2x(this.e);
    this.sel.style.left = `${x0}px`;
    this.sel.style.width = `${Math.max(4, x1 - x0)}px`;
    if (this.play !== null && this.play >= this.w0 && this.play <= this.w1) {
      this.playEl.hidden = false;
      this.playEl.style.left = `${this.t2x(this.play)}px`;
    } else this.playEl.hidden = true;
  }

  draw() {
    const cv = this.canvas;
    const w = this.wrap.clientWidth;
    if (!w) return;
    const dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(HEIGHT * dpr);
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#07090d";
    ctx.fillRect(0, 0, w, HEIGHT);
    const span = this.w1 - this.w0;
    // vignettes
    const sp = this.data.sprite;
    if (this.sprite && sp) {
      const tw = (THUMB_H * sp.w) / sp.h;
      const n = Math.ceil(w / tw);
      for (let i = 0; i < n; i++) {
        const t = this.w0 + ((i + 0.5) * tw / w) * span;
        const idx = Math.min(sp.count - 1, Math.max(0, Math.round(t / sp.interval)));
        const col = idx % sp.cols;
        const row = Math.floor(idx / sp.cols);
        ctx.drawImage(this.sprite, col * sp.w, row * sp.h, sp.w, sp.h, i * tw, 0, tw, THUMB_H);
      }
      ctx.fillStyle = "rgba(7,9,13,.25)";
      ctx.fillRect(0, 0, w, THUMB_H);
    }
    // énergie audio
    const au = this.data.audio || {};
    const vals = au.values || [];
    const hop = au.hop || 0.5;
    const base = HEIGHT - 4;
    const ah = HEIGHT - THUMB_H - 8;
    ctx.fillStyle = "rgba(124,92,255,.75)";
    const step = Math.max(1, Math.floor(span / hop / w));
    for (let i = Math.floor(this.w0 / hop); i < Math.min(vals.length, Math.ceil(this.w1 / hop)); i += step) {
      const x = ((i * hop - this.w0) / span) * w;
      const bw = Math.max(1, ((hop * step) / span) * w - 1);
      const v = Math.max(0.04, vals[i] || 0);
      ctx.fillRect(x, base - v * ah, bw, v * ah);
    }
    // mots
    ctx.fillStyle = "rgba(34,211,238,.55)";
    for (const wd of this.data.words || []) {
      if (wd[1] < this.w0 || wd[0] > this.w1) continue;
      const x0 = ((wd[0] - this.w0) / span) * w;
      const x1 = ((wd[1] - this.w0) / span) * w;
      ctx.fillRect(x0, HEIGHT - 3, Math.max(1, x1 - x0 - 0.5), 3);
    }
    // coupes : petits repères en haut
    ctx.fillStyle = "rgba(255,255,255,.7)";
    for (const c of this.data.cuts || []) {
      if (c < this.w0 || c > this.w1) continue;
      ctx.fillRect(((c - this.w0) / span) * w - 0.5, 0, 1.5, 6);
    }
    const ticks = 6;
    this.scale.replaceChildren(...Array.from({ length: ticks + 1 }, (_, i) => h("span", {}, fmtTime(this.w0 + (span * i) / ticks))));
    this.position();
  }

  destroy() { this.ro.disconnect(); }
}
