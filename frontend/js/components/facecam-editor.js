// Correction manuelle du rectangle de la facecam (coordonnées source).
import { api } from "../api.js";
import { h, modal, toast } from "../ui.js";

export function openFacecamEditor(project, clip, onSaved) {
  const W = project.width;
  const H = project.height;
  const fc = clip.facecam && clip.facecam.rect ? clip.facecam.rect : [Math.round(W * 0.72), Math.round(H * 0.04), Math.round(W * 0.26), Math.round(H * 0.26)];
  let rect = [...fc];
  const video = h("video", { src: project.preview_url, preload: "auto", muted: true });
  video.muted = true;
  const box = h("div", { class: "fc-rect" }, ["nw", "ne", "sw", "se"].map((c) => h("span", { class: `h ${c}`, "data-c": c })));
  const stage = h("div", { class: "fc-stage", style: { aspectRatio: `${W}/${H}` } }, video, box);
  const scrub = h("input", { type: "range", min: 0, max: 1000, value: 0, class: "seek", "aria-label": "Choisir l'image" });
  const info = h("span", { class: "muted small mono" });
  video.addEventListener("loadedmetadata", () => {
    video.currentTime = Math.min(video.duration - 0.1, clip.start + 1);
    scrub.value = Math.round((video.currentTime / video.duration) * 1000);
  });
  scrub.addEventListener("input", () => { if (video.duration) video.currentTime = (scrub.value / 1000) * video.duration; });

  const place = () => {
    const sw = stage.clientWidth / W;
    const sh = stage.clientHeight / H;
    Object.assign(box.style, { left: `${rect[0] * sw}px`, top: `${rect[1] * sh}px`, width: `${rect[2] * sw}px`, height: `${rect[3] * sh}px` });
    info.textContent = `x ${rect[0]} · y ${rect[1]} · ${rect[2]}×${rect[3]} px`;
  };
  const ro = new ResizeObserver(place);
  ro.observe(stage);

  let drag = null;
  box.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    drag = { c: ev.target.dataset.c || "move", x0: ev.clientX, y0: ev.clientY, r0: [...rect] };
    box.setPointerCapture(ev.pointerId);
  });
  box.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    const sx = W / stage.clientWidth;
    const sy = H / stage.clientHeight;
    const dx = (ev.clientX - drag.x0) * sx;
    const dy = (ev.clientY - drag.y0) * sy;
    let [x, y, w, hh] = drag.r0;
    if (drag.c === "move") { x += dx; y += dy; }
    else {
      if (drag.c.includes("w")) { x += dx; w -= dx; }
      if (drag.c.includes("e")) w += dx;
      if (drag.c.includes("n")) { y += dy; hh -= dy; }
      if (drag.c.includes("s")) hh += dy;
    }
    w = Math.max(32, Math.min(W, w));
    hh = Math.max(32, Math.min(H, hh));
    x = Math.max(0, Math.min(W - w, x));
    y = Math.max(0, Math.min(H - hh, y));
    rect = [Math.round(x), Math.round(y), Math.round(w), Math.round(hh)];
    place();
  });
  box.addEventListener("pointerup", () => { drag = null; });

  modal({
    title: "Zone de la facecam",
    wide: true,
    body: h("div", { class: "stack" },
      h("p", { class: "muted" }, "Ajuste le cadre sur la webcam du streamer. Cette zone sera affichée en haut du clip pour tous les clips du projet en layout « Facecam »."),
      stage, scrub, info),
    actions: [
      {
        label: "Détection automatique", class: "ghost", onClick: async () => {
          try { const r = await api.setFacecam(project.id, { reset: true }); toast(r.facecam.found ? "Détection automatique rétablie" : "Aucune facecam détectée automatiquement", "ok"); onSaved && onSaved(r.facecam); } catch (e) { toast(e.message, "error"); return false; }
          return true;
        },
      },
      { label: "Annuler", class: "ghost" },
      {
        label: "Enregistrer", class: "primary", onClick: async () => {
          try { const r = await api.setFacecam(project.id, { rect }); toast("Zone facecam enregistrée", "ok"); onSaved && onSaved(r.facecam); } catch (e) { toast(e.message, "error"); return false; }
          return true;
        },
      },
    ],
    onClose: () => { ro.disconnect(); video.removeAttribute("src"); video.load(); },
  });
}
