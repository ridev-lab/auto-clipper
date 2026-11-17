// Aperçu instantané d'un clip dans le navigateur : la vidéo proxy est
// dessinée dans un canvas 9:16 avec exactement la même géométrie que le
// rendu ffmpeg, et les sous-titres sont dessinés à partir des mêmes données
// que le fichier ASS.
import { fmtTime, h, icon } from "../ui.js";

const fontFamily = (file) => `AC-${String(file || "").replace(/\.[^.]+$/, "")}`;

function interpTrack(points, t) {
  if (!points || !points.length) return null;
  if (t <= points[0][0]) return points[0][1];
  for (let i = 1; i < points.length; i++) {
    if (t <= points[i][0]) {
      const [t0, x0] = points[i - 1];
      const [t1, x1] = points[i];
      return x0 + ((x1 - x0) * (t - t0)) / Math.max(1e-6, t1 - t0);
    }
  }
  return points[points.length - 1][1];
}

export class ClipPlayer {
  constructor({ previewUrl, clip, onTime }) {
    this.clip = clip;
    this.onTime = onTime;
    this.loop = true;
    this.mode = "live";
    this.video = h("video", { src: previewUrl, preload: "auto", playsinline: true });
    this.video.crossOrigin = "anonymous";
    this.canvas = h("canvas", { width: 540, height: 960, "aria-label": "Aperçu du clip" });
    this.ctx = this.canvas.getContext("2d");
    this.finalVideo = h("video", { controls: true, playsinline: true, hidden: true });
    this.safe = h("div", { class: "safe", hidden: true },
      h("i", { style: { left: 0, right: 0, bottom: 0, height: "20%" } }),
      h("i", { style: { right: 0, width: "15%", top: "40%", bottom: "20%" } }),
      h("i", { style: { left: 0, right: 0, top: 0, height: "7%" } }));
    this.loading = h("div", { class: "loading" }, h("span", { class: "spinner" }));
    const frame = h("div", { class: "player-frame" }, this.canvas, this.finalVideo, this.safe, this.loading);
    this.playBtn = h("button", { class: "btn icon", title: "Lecture / pause (Espace)", "aria-label": "Lecture" }, icon("play"));
    this.timeEl = h("span", { class: "time" }, "0:00.0 / 0:00.0");
    this.seek = h("input", { class: "seek", type: "range", min: 0, max: 1000, value: 0, "aria-label": "Position" });
    this.muteBtn = h("button", { class: "btn icon ghost", title: "Son", "aria-label": "Son" }, icon("volume"));
    const safeBtn = h("button", { class: "btn sm ghost", title: "Afficher les zones masquées par l'interface TikTok / Reels" }, "Zones TikTok");
    const restart = h("button", { class: "btn icon ghost", title: "Revenir au début", "aria-label": "Revenir au début" }, icon("restart"));
    this.liveCtrls = h("div", {}, h("div", { class: "player-ctrls" }, this.playBtn, restart, this.timeEl, h("span", { class: "grow" }), safeBtn, this.muteBtn), this.seek);
    this.tabLive = h("button", { class: "active" }, "Aperçu instantané");
    this.tabFinal = h("button", {}, "Rendu final");
    this.modeSwitch = h("div", { class: "mode-switch" }, h("div", { class: "tabs" }, this.tabLive, this.tabFinal));
    this.el = h("div", { class: "player" }, frame, this.liveCtrls, this.modeSwitch);

    this.playBtn.onclick = () => this.toggle();
    restart.onclick = () => { this.seekRel(0); };
    this.muteBtn.onclick = () => { this.video.muted = !this.video.muted; this.muteBtn.replaceChildren(icon(this.video.muted ? "mute" : "volume")); };
    safeBtn.onclick = () => { this.safe.hidden = !this.safe.hidden; safeBtn.classList.toggle("active", !this.safe.hidden); };
    this.seek.addEventListener("input", () => this.seekRel((this.seek.value / 1000) * this.duration()));
    this.tabLive.onclick = () => this.setMode("live");
    this.tabFinal.onclick = () => this.setMode("final");
    this.video.addEventListener("loadeddata", () => { this.loading.hidden = true; this.seekRel(0); });
    this.video.addEventListener("seeked", () => this.draw());
    this.video.addEventListener("play", () => { this.playBtn.replaceChildren(icon("pause")); this.loop_(); });
    this.video.addEventListener("pause", () => { this.playBtn.replaceChildren(icon("play")); this.draw(); });
    this.video.addEventListener("error", () => { this.loading.replaceChildren(h("span", { class: "muted small" }, "Aperçu indisponible")); });
    this.fontsReady = this.loadFonts();
    this.updateFinal();
  }

  async loadFonts() {
    const c = this.clip.captions;
    const fams = [fontFamily(c.preset.font_file), fontFamily(c.hook.font_file)];
    try { await Promise.all(fams.map((f) => document.fonts.load(`40px "${f}"`))); } catch { /* rien */ }
    this.draw();
  }

  duration() { return Math.max(0.1, this.clip.end - this.clip.start); }
  rel() { return this.video.currentTime - this.clip.start; }

  update(clip) {
    const rangeChanged = clip.start !== this.clip.start || clip.end !== this.clip.end;
    this.clip = clip;
    this.loadFonts();
    this.updateFinal();
    if (rangeChanged && (this.video.currentTime < clip.start || this.video.currentTime > clip.end)) this.seekRel(0);
    this.draw();
  }

  setRange(start, end) {
    this.clip = { ...this.clip, start, end };
    if (this.video.currentTime < start || this.video.currentTime > end) this.seekRel(0);
    this.draw();
  }

  updateFinal() {
    this.tabFinal.disabled = !this.clip.file_url;
    this.tabFinal.title = this.clip.file_url ? (this.clip.dirty ? "Dernier rendu (avant tes modifications)" : "Fichier MP4 rendu") : "Pas encore rendu";
    if (this.clip.file_url && this.finalVideo.getAttribute("src") !== this.clip.file_url) this.finalVideo.src = this.clip.file_url;
    if (!this.clip.file_url && this.mode === "final") this.setMode("live");
  }

  setMode(m) {
    this.mode = m;
    this.tabLive.classList.toggle("active", m === "live");
    this.tabFinal.classList.toggle("active", m === "final");
    this.finalVideo.hidden = m !== "final";
    this.canvas.hidden = m === "final";
    this.liveCtrls.hidden = m === "final";
    if (m === "final") { this.video.pause(); this.finalVideo.play().catch(() => {}); } else this.finalVideo.pause();
  }

  toggle() {
    if (this.mode === "final") { this.finalVideo.paused ? this.finalVideo.play() : this.finalVideo.pause(); return; }
    if (this.video.paused) {
      if (this.rel() >= this.duration() - 0.05 || this.rel() < 0) this.video.currentTime = this.clip.start;
      this.video.play().catch(() => {});
    } else this.video.pause();
  }

  pause() { this.video.pause(); this.finalVideo.pause(); }

  seekRel(t) {
    this.video.currentTime = this.clip.start + Math.max(0, Math.min(this.duration(), t));
    this.draw();
  }

  seekAbs(t) { this.seekRel(t - this.clip.start); }

  loop_() {
    if (this.video.paused) return;
    if (this.video.currentTime >= this.clip.end) {
      if (this.loop) this.video.currentTime = this.clip.start; else this.video.pause();
    }
    this.draw();
    requestAnimationFrame(() => this.loop_());
  }

  draw() {
    const { ctx, canvas } = this;
    const c = this.clip;
    const g = c.geometry;
    const k = canvas.width / g.width;
    const t = this.rel();
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    const v = this.video;
    if (v.readyState >= 2 && v.videoWidth) {
      const ps = v.videoWidth / g.src_w;
      for (const L of g.layers) {
        let [sx, sy, sw, sh] = L.src;
        const [dx, dy, dw, dh] = L.dst;
        if (L.track && c.track) {
          const x = interpTrack(c.track, t);
          if (x !== null) sx = x;
        }
        ctx.save();
        if (L.blur) {
          ctx.filter = `blur(${Math.max(2, g.blur_sigma * k * 1.1)}px) brightness(0.92)`;
          // léger débordement pour éviter les bords transparents du flou
          const m = 20 * k;
          ctx.drawImage(v, sx * ps, sy * ps, sw * ps, sh * ps, dx * k - m, dy * k - m, dw * k + 2 * m, dh * k + 2 * m);
        } else {
          ctx.drawImage(v, sx * ps, sy * ps, sw * ps, sh * ps, dx * k, dy * k, dw * k, dh * k);
        }
        ctx.restore();
      }
    }
    this.drawCaptions(t, k);
    this.drawHook(t, k);
    const d = this.duration();
    this.timeEl.textContent = `${fmtTime(Math.max(0, t), 1)} / ${fmtTime(d, 1)}`;
    if (document.activeElement !== this.seek) this.seek.value = Math.round((Math.max(0, t) / d) * 1000);
    this.onTime && this.onTime(this.video.currentTime, t);
  }

  drawCaptions(t, k) {
    const cap = this.clip.captions;
    const p = cap.preset;
    const g = cap.groups.find((gr) => t >= gr.s && t < gr.e);
    if (!g) return;
    const { ctx } = this;
    const size = p.font_size * g.scale * k;
    ctx.save();
    ctx.font = `${size}px "${fontFamily(p.font_file)}"`;
    ctx.textBaseline = "middle";
    ctx.lineJoin = "round";
    const texts = g.words.map((w) => (p.uppercase ? w.t.toUpperCase() : w.t));
    const space = ctx.measureText(" ").width;
    const widths = texts.map((s) => ctx.measureText(s).width);
    const total = widths.reduce((a, b) => a + b, 0) + space * (texts.length - 1);
    let active = 0;
    g.words.forEach((w, i) => { if (i === 0 || w.s <= t) active = i; });
    let x = cap.x * k - total / 2;
    const y = cap.y * k;
    const kwColor = (p.keyword_colors && p.keyword_colors[0]) || p.active_color;
    texts.forEach((s, i) => {
      const w = g.words[i];
      const isActive = i === active;
      let sc = 1;
      if (isActive && p.active_scale !== 100) {
        const since = Math.max(0, t - (i === 0 ? g.s : w.s));
        sc = 1 + (p.active_scale / 100 - 1) * Math.min(1, since / 0.09);
      }
      ctx.save();
      ctx.translate(x + widths[i] / 2, y);
      ctx.scale(sc, sc);
      if (p.shadow) {
        ctx.fillStyle = "rgba(0,0,0,.6)";
        ctx.lineWidth = p.outline * 2 * k;
        ctx.strokeStyle = "rgba(0,0,0,.6)";
        ctx.strokeText(s, -widths[i] / 2 + p.shadow * k, p.shadow * k);
      }
      ctx.lineWidth = p.outline * 2 * k;
      ctx.strokeStyle = p.outline_color;
      ctx.strokeText(s, -widths[i] / 2, 0);
      ctx.fillStyle = isActive ? p.active_color : (w.kw ? kwColor : p.color);
      ctx.fillText(s, -widths[i] / 2, 0);
      ctx.restore();
      x += widths[i] + space;
    });
    ctx.restore();
  }

  drawHook(t, k) {
    const hk = this.clip.captions.hook;
    if (!hk.lines || !hk.lines.length || t >= hk.duration || t < 0) return;
    const { ctx, canvas } = this;
    const alpha = Math.min(1, t / 0.12, (hk.duration - t) / 0.25);
    ctx.save();
    ctx.globalAlpha = Math.max(0, alpha);
    ctx.font = `${hk.font_size * k}px "${fontFamily(hk.font_file)}"`;
    ctx.textBaseline = "middle";
    const lineH = hk.font_size * k * 1.22;
    const pad = 16 * k;
    const y0 = hk.y * k - ((hk.lines.length - 1) * lineH) / 2;
    hk.lines.forEach((line, i) => {
      const w = ctx.measureText(line).width;
      const cy = y0 + i * lineH;
      ctx.fillStyle = hk.box_color;
      ctx.fillRect(canvas.width / 2 - w / 2 - pad, cy - lineH / 2 - pad * 0.35, w + 2 * pad, lineH + pad * 0.7);
      ctx.fillStyle = hk.color;
      ctx.fillText(line, canvas.width / 2 - w / 2, cy);
    });
    ctx.restore();
  }

  destroy() {
    this.video.pause();
    this.finalVideo.pause();
    this.video.removeAttribute("src");
    this.video.load();
  }
}
