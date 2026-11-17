// Page « Nouveau projet » : lien YouTube ou upload + options.
import { api, getConfig } from "../api.js";
import { Uploader } from "../upload.js";
import { debounce, fmtBytes, fmtTime, h, icon, parseTime, store, toast } from "../ui.js";

const YT_RE = /^(https?:\/\/)?(www\.|m\.|music\.)?(youtube\.com\/(watch\?|shorts\/|live\/|embed\/)|youtu\.be\/)\S+/i;
const LAYOUT_LABELS = { auto: "Auto (facecam si détectée)", facecam: "Facecam en haut", blur: "Fond flou", track: "Suivi (recadrage dynamique)" };

export async function renderNew(root) {
  const cfg = await getConfig();
  const d = cfg.defaults;
  const state = {
    mode: store("new:mode") || "youtube",
    yt: null, ytUrl: "", uploadId: null, uploader: null, file: null,
  };

  // ----- source : YouTube -----
  const ytInput = h("input", { class: "input big", type: "url", placeholder: "https://www.youtube.com/watch?v=…", "aria-label": "Lien YouTube", autocomplete: "off" });
  const ytInfo = h("div");
  let ytAbort = null;
  const checkYt = debounce(async () => {
    const url = ytInput.value.trim();
    state.yt = null;
    updateSubmit();
    if (!url) { ytInfo.replaceChildren(); return; }
    if (!YT_RE.test(url)) { ytInfo.replaceChildren(h("p", { class: "err-text", style: { marginTop: "10px" } }, "Ce n'est pas un lien YouTube valide.")); return; }
    ytAbort && ytAbort.abort();
    ytAbort = new AbortController();
    ytInfo.replaceChildren(h("div", { class: "yt-preview" }, h("div", { class: "skeleton", style: { width: "140px", height: "79px" } }), h("div", { class: "grow" }, h("span", { class: "muted" }, "Récupération des infos de la vidéo…"))));
    try {
      const info = await api.ytInfo(url, ytAbort.signal);
      state.yt = info;
      state.ytUrl = url;
      ytInfo.replaceChildren(h("div", { class: "yt-preview" },
        info.thumbnail ? h("img", { src: info.thumbnail, alt: "", referrerpolicy: "no-referrer" }) : null,
        h("div", { class: "grow" },
          h("div", { style: { fontWeight: 650 } }, info.title),
          h("div", { class: "muted small" }, [info.uploader, info.duration ? fmtTime(info.duration) : null].filter(Boolean).join(" · ")),
          info.duration > 3 * 3600 ? h("div", { class: "hint", style: { color: "var(--warn)" } }, "Vidéo très longue : pense à définir une plage à analyser dans les options avancées.") : null,
          info.is_live ? h("div", { class: "err-text" }, "Live en cours : attends la fin de la diffusion.") : null)));
      if (info.duration > 3 * 3600) adv.open = true;
    } catch (e) {
      if (e.name === "AbortError") return;
      ytInfo.replaceChildren(h("p", { class: "err-text", style: { marginTop: "10px" } }, e.message));
    }
    updateSubmit();
  }, 450);
  ytInput.addEventListener("input", checkYt);
  ytInput.addEventListener("paste", () => setTimeout(() => checkYt.flush(), 0));
  const ytZone = h("div", { class: "source-zone" }, h("div", { class: "yt-row" }, ytInput), ytInfo);

  // ----- source : fichier -----
  const fileInput = h("input", { type: "file", accept: ".mp4,.mov,.mkv,.webm,.m4v,video/*", hidden: true });
  const drop = h("div", { class: "dropzone", tabindex: "0", role: "button", "aria-label": "Choisir une vidéo" },
    h("div", { class: "big-icon" }, icon("upload")),
    h("div", { style: { fontWeight: 650, fontSize: "15px" } }, "Glisse ta vidéo ici ou clique pour choisir"),
    h("div", { class: "hint", style: { marginTop: "4px" } }, "MP4, MOV, MKV, WEBM — plusieurs Go acceptés, l'envoi reprend s'il est interrompu"));
  const fileBox = h("div");
  const fileZone = h("div", { class: "source-zone" }, drop, fileBox, fileInput);
  drop.addEventListener("click", () => fileInput.click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) pickFile(e.dataTransfer.files[0]); });
  fileInput.addEventListener("change", () => fileInput.files[0] && pickFile(fileInput.files[0]));

  function pickFile(f) {
    const ext = (f.name.split(".").pop() || "").toLowerCase();
    if (!["mp4", "mov", "mkv", "webm", "m4v"].includes(ext)) { toast(`Format non supporté (.${ext}). Formats acceptés : MP4, MOV, MKV, WEBM.`, "error"); return; }
    state.uploader && state.uploader.cancel();
    state.file = f;
    state.uploadId = null;
    drop.hidden = true;
    const bar = h("i");
    const line = h("div", { class: "muted small" }, "Préparation de l'envoi…");
    const cancelBtn = h("button", { class: "btn sm ghost", title: "Retirer ce fichier", onclick: () => { state.uploader && state.uploader.cancel(); state.uploader = null; state.file = null; state.uploadId = null; fileBox.replaceChildren(); drop.hidden = false; fileInput.value = ""; updateSubmit(); } }, icon("x"), "Retirer");
    fileBox.replaceChildren(h("div", { class: "upload-file" },
      h("div", { style: { color: "var(--accent-2)" } }, icon("film")),
      h("div", { class: "meta" }, h("div", { class: "name" }, f.name), line, h("div", { class: "progress" }, bar)),
      cancelBtn));
    const up = new Uploader(f, {
      onProgress: ({ loaded, total, speed, eta }) => {
        bar.style.width = `${(loaded / total) * 100}%`;
        line.textContent = `${fmtBytes(loaded)} / ${fmtBytes(total)} · ${fmtBytes(speed)}/s · reste ${fmtTime(eta)}`;
      },
      onDone: (id) => {
        state.uploadId = id;
        bar.style.width = "100%";
        line.textContent = `${fmtBytes(f.size)} — envoi terminé ✓`;
        updateSubmit();
        if (pendingSubmit) submit();
      },
      onError: (e) => { line.textContent = "Échec de l'envoi : " + e.message; line.className = "err-text"; toast(e.message, "error"); },
    });
    state.uploader = up;
    up.start();
    updateSubmit();
  }

  // ----- onglets -----
  const tabYt = h("button", { type: "button" }, icon("youtube"), "Lien YouTube");
  const tabFile = h("button", { type: "button" }, icon("upload"), "Fichier vidéo");
  const setMode = (m) => {
    state.mode = m;
    store("new:mode", m);
    tabYt.classList.toggle("active", m === "youtube");
    tabFile.classList.toggle("active", m === "upload");
    ytZone.hidden = m !== "youtube";
    fileZone.hidden = m !== "upload";
    updateSubmit();
    if (m === "youtube") ytInput.focus();
  };
  tabYt.onclick = () => setMode("youtube");
  tabFile.onclick = () => setMode("upload");

  // ----- options -----
  const saved = store("new:options") || {};
  const val = (k) => (saved[k] !== undefined ? saved[k] : d[k]);
  const clipsIn = h("input", { type: "number", min: 1, max: 15, value: val("clips"), "aria-label": "Nombre de clips" });
  const stepper = h("div", { class: "stepper" },
    h("button", { type: "button", "aria-label": "Moins", onclick: () => { clipsIn.value = Math.max(1, +clipsIn.value - 1); } }, "−"),
    clipsIn,
    h("button", { type: "button", "aria-label": "Plus", onclick: () => { clipsIn.value = Math.min(15, +clipsIn.value + 1); } }, "+"));
  const minIn = h("input", { class: "input num", type: "number", min: 5, max: 170, value: val("min_duration") });
  const maxIn = h("input", { class: "input num", type: "number", min: 6, max: 180, value: val("max_duration") });
  const layoutSel = h("select", { class: "input" }, cfg.layouts.map((l) => h("option", { value: l, selected: val("layout") === l }, LAYOUT_LABELS[l] || l)));
  const langSel = h("select", { class: "input" }, [["auto", "Détection automatique"], ["fr", "Français"], ["en", "Anglais"]].map(([v, l]) => h("option", { value: v, selected: val("language") === v }, l)));
  const presetSel = h("select", { class: "input" }, cfg.presets.map((p) => h("option", { value: p.id, selected: val("subtitle_preset") === p.id }, p.label || p.id)));
  const hookChk = h("input", { type: "checkbox", checked: val("hook_title") });
  const rangeStart = h("input", { class: "input time", placeholder: "0:00", "aria-label": "Début de la plage" });
  const rangeEnd = h("input", { class: "input time", placeholder: "fin", "aria-label": "Fin de la plage" });

  const adv = h("details", { class: "adv" },
    h("summary", {}, "Options avancées"),
    h("div", { class: "grid-form" },
      h("label", { class: "field" }, "Layout", layoutSel),
      h("label", { class: "field" }, "Langue", langSel),
      h("label", { class: "field" }, "Style des sous-titres", presetSel),
      h("div", { class: "field" }, h("span", { class: "field-label", style: { color: "var(--text-2)", fontWeight: 500, fontSize: "13px" } }, "Plage à analyser (streams longs)"),
        h("div", { class: "row" }, rangeStart, h("span", { class: "muted" }, "→"), rangeEnd)),
      h("div", { class: "field", style: { justifyContent: "flex-end" } }, h("label", { class: "switch" }, hookChk, "Titre hook pendant 3 s"))));

  // ----- envoi -----
  const submitBtn = h("button", { class: "btn primary big", type: "button" }, icon("sparkles"), "Générer les clips");
  const submitHint = h("span", { class: "hint" });
  let pendingSubmit = false;
  function updateSubmit() {
    let ok = false;
    let hint = "";
    if (state.mode === "youtube") {
      ok = !!state.yt && !state.yt.is_live;
      hint = ok ? "" : "Colle un lien YouTube pour commencer.";
    } else if (state.file) {
      ok = true;
      hint = state.uploadId ? "" : "Tu peux lancer maintenant : le traitement démarrera à la fin de l'envoi.";
    } else {
      hint = "Choisis un fichier vidéo pour commencer.";
    }
    submitBtn.disabled = !ok || pendingSubmit;
    submitHint.textContent = pendingSubmit ? "Lancement dès que l'envoi est terminé…" : hint;
  }

  async function submit() {
    const opts = {
      clips: +clipsIn.value, min_duration: +minIn.value, max_duration: +maxIn.value,
      layout: layoutSel.value, language: langSel.value, subtitle_preset: presetSel.value, hook_title: hookChk.checked,
    };
    if (opts.max_duration <= opts.min_duration) { toast("La durée max doit être supérieure à la durée min.", "error"); return; }
    const rs = rangeStart.value.trim() ? parseTime(rangeStart.value) : null;
    const re = rangeEnd.value.trim() ? parseTime(rangeEnd.value) : null;
    if ((rs !== null && isNaN(rs)) || (re !== null && isNaN(re))) { toast("Plage invalide : utilise le format 1:23:45 ou 12:30.", "error"); return; }
    store("new:options", opts);
    opts.range_start = rs;
    opts.range_end = re;
    let body;
    if (state.mode === "youtube") body = { source_type: "youtube", url: state.ytUrl, title: state.yt.title, options: opts };
    else {
      if (!state.uploadId) { pendingSubmit = true; updateSubmit(); return; }
      body = { source_type: "upload", upload_id: state.uploadId, options: opts };
    }
    submitBtn.disabled = true;
    try {
      const r = await api.createProject(body);
      pendingSubmit = false;
      location.hash = `#/project/${r.project.id}`;
    } catch (e) {
      pendingSubmit = false;
      toast(e.message, "error", 7000);
      updateSubmit();
    }
  }
  submitBtn.onclick = submit;

  root.append(
    h("section", { class: "hero" },
      h("h1", {}, "Transforme une vidéo en ", h("em", {}, "clips viraux")),
      h("p", {}, "Colle un lien YouTube ou importe ta vidéo : l'IA locale repère les meilleurs moments, recadre en 9:16 et ajoute des sous-titres animés.")),
    h("div", { class: "card new-card" },
      h("div", { class: "tabs", role: "tablist" }, tabYt, tabFile),
      ytZone, fileZone,
      h("div", { class: "grid-form" },
        h("div", { class: "field" }, h("span", { style: { color: "var(--text-2)", fontWeight: 500, fontSize: "13px" } }, "Nombre de clips"), stepper),
        h("div", { class: "field" }, h("span", { style: { color: "var(--text-2)", fontWeight: 500, fontSize: "13px" } }, "Durée d'un clip (secondes)"),
          h("div", { class: "row" }, minIn, h("span", { class: "muted" }, "à"), maxIn))),
      adv,
      h("div", { class: "submit-row" }, submitBtn, h("div", { class: "grow" }, submitHint))),
  );
  setMode(state.mode);
  return () => { /* l'upload continue en arrière-plan si on quitte la page */ };
}
