// Ajout d'un clip manuel : sélection d'une plage sur la vidéo source.
import { api } from "../api.js";
import { fmtTime, h, icon, modal, parseTime, toast } from "../ui.js";
import { Timeline } from "./timeline.js";

export async function openManualClip(project, onCreated) {
  let data;
  try { data = await api.timeline(project.id); } catch (e) { toast(e.message, "error"); return; }
  const dur = data.duration;
  let s = Math.min(dur * 0.1, Math.max(0, dur - 30));
  let e = Math.min(dur, s + 30);
  const video = h("video", { src: project.preview_url, controls: true, preload: "auto", style: { width: "100%", borderRadius: "10px", background: "#000", maxHeight: "46vh" } });
  const sIn = h("input", { class: "input time", value: fmtTime(s, 1) });
  const eIn = h("input", { class: "input time", value: fmtTime(e, 1) });
  const lenEl = h("span", { class: "muted" });
  const tl = new Timeline({
    data, start: s, end: e, window: [0, dur],
    onChange: (a, b) => { s = a; e = b; sync(); video.currentTime = a; },
    onSeek: (t) => { video.currentTime = t; },
  });
  function sync() {
    sIn.value = fmtTime(s, 1);
    eIn.value = fmtTime(e, 1);
    lenEl.textContent = `Durée : ${(e - s).toFixed(1)} s`;
  }
  const fromInputs = () => {
    const a = parseTime(sIn.value);
    const b = parseTime(eIn.value);
    if (isNaN(a) || isNaN(b) || b - a < 1) { toast("Plage invalide", "error"); sync(); return; }
    s = Math.max(0, a); e = Math.min(dur, b);
    tl.setRange(s, e);
    sync();
  };
  sIn.addEventListener("change", fromInputs);
  eIn.addEventListener("change", fromInputs);
  video.addEventListener("timeupdate", () => tl.setPlayhead(video.currentTime));
  const setStartBtn = h("button", { class: "btn sm", onclick: () => { s = video.currentTime; if (e - s < 1 || e - s > 180) e = Math.min(dur, s + 30); tl.setRange(s, e); sync(); } }, "Début = lecture");
  const setEndBtn = h("button", { class: "btn sm", onclick: () => { if (video.currentTime - s >= 1) { e = video.currentTime; tl.setRange(s, e); sync(); } } }, "Fin = lecture");
  const zoomIn = h("button", { class: "btn sm icon", title: "Zoomer (Ctrl + molette)", onclick: () => tl.zoom(0.5) }, icon("zoomin"));
  const zoomOut = h("button", { class: "btn sm icon", title: "Dézoomer", onclick: () => tl.zoom(2) }, icon("zoomout"));
  sync();
  const m = modal({
    title: "Ajouter un clip manuel",
    wide: true,
    body: h("div", { class: "stack" },
      video,
      tl.el,
      h("div", { class: "tl-bar" }, h("span", { class: "muted" }, "Début"), sIn, h("span", { class: "muted" }, "Fin"), eIn, lenEl,
        h("span", { class: "grow" }), setStartBtn, setEndBtn, zoomOut, zoomIn),
      h("p", { class: "hint" }, "Glisse les poignées violettes ou déplace la sélection. Les bornes s'accrochent aux mots prononcés.")),
    actions: [
      { label: "Annuler", class: "ghost" },
      {
        label: "Créer le clip", class: "primary", onClick: async () => {
          try {
            const r = await api.addClip(project.id, { start: s, end: e });
            toast("Clip créé : rendu en cours", "ok");
            onCreated && onCreated(r.clip);
          } catch (err) { toast(err.message, "error"); return false; }
          return true;
        },
      },
    ],
    onClose: () => { video.pause(); tl.destroy(); },
  });
  return m;
}
