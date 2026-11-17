// Éditeur d'un clip : aperçu instantané, découpe, layout, sous-titres,
// style, textes de publication, re-rendu.
import { api, getConfig } from "../api.js";
import { openFacecamEditor } from "../components/facecam-editor.js";
import { ClipPlayer } from "../components/player.js";
import { Timeline } from "../components/timeline.js";
import { confirmDialog, copyText, debounce, fmtTime, h, icon, parseTime, scoreClass, toast } from "../ui.js";

const LAYOUTS = [
  ["facecam", "Facecam", "Webcam en haut, jeu en bas", [["0", "35%", "#3b82f6"], ["35%", "65%", "#334155"]]],
  ["blur", "Fond flou", "Vidéo entière sur fond flouté", [["0", "100%", "#1e293b"], ["36%", "28%", "#475569"]]],
  ["track", "Suivi", "Recadrage qui suit l'action", [["0", "100%", "#334155"]]],
];

export async function renderEditor(root, pid, cid) {
  const cfg = await getConfig();
  const [project, tlData] = await Promise.all([api.project(pid), api.timeline(pid)]);
  let clip = await api.clip(cid);
  if (!project.preview_url) throw new Error("Le projet n'est pas encore prêt.");
  let es = null;
  let renderJob = null;

  // ----- en-tête -----
  const hookTitle = h("h1", { style: { fontSize: "22px" } });
  const metaLine = h("div", { class: "sub" });
  const renderBtn = h("button", { class: "btn primary" }, icon("refresh"), "Re-rendre");
  const dlBtn = h("a", { class: "btn", download: "" }, icon("download"), "Télécharger");
  const delBtn = h("button", { class: "btn ghost danger", title: "Supprimer ce clip" }, icon("trash"));
  renderBtn.onclick = async () => {
    await saveNow();
    try { const r = await api.renderClip(cid); renderJob = r.job; setRenderState("queued", 0); } catch (e) { toast(e.message, "error"); }
  };
  delBtn.onclick = async () => {
    if (!(await confirmDialog("Supprimer ce clip ?", "Le fichier MP4 rendu sera supprimé.", "Supprimer", true))) return;
    try { await api.deleteClip(cid); location.hash = `#/project/${pid}`; } catch (e) { toast(e.message, "error"); }
  };

  // ----- lecteur -----
  const player = new ClipPlayer({ previewUrl: project.preview_url, clip, onTime: (abs) => onTime(abs) });
  window.__editor = { player }; // inspection / tests

  // ----- sauvegarde -----
  let pending = {};
  const flush = async () => {
    const body = pending;
    pending = {};
    if (!Object.keys(body).length) return;
    try {
      clip = await api.patchClip(cid, body);
      player.update(clip);
      refreshHead();
      if ("start" in body || "end" in body) renderWords();
    } catch (e) { toast(e.message, "error"); }
  };
  const save = debounce(flush, 500);
  const saveNow = () => save.flush();
  const queue = (fields) => { Object.assign(pending, fields); Object.assign(clip, fields); refreshHead(); save(); };

  function refreshHead() {
    hookTitle.textContent = clip.hook || clip.title || "Clip";
    metaLine.replaceChildren(
      h("span", { class: `score ${scoreClass(clip.score)}`, style: { position: "static" } }, clip.manual ? "manuel" : clip.score),
      h("span", {}, `${fmtTime(clip.start, 1)} → ${fmtTime(clip.end, 1)} (${(clip.end - clip.start).toFixed(1)} s)`),
      clip.dirty ? h("span", { class: "chip interrupted" }, "Modifications non rendues") : clip.file ? h("span", { class: "chip done" }, "À jour") : h("span", { class: "chip" }, "Pas encore rendu"));
    renderBtn.classList.toggle("pulse", !!clip.dirty && !renderJob);
    if (clip.file_url) { dlBtn.href = `/api/clips/${cid}/download`; dlBtn.hidden = false; } else dlBtn.hidden = true;
  }

  function setRenderState(status, pct) {
    if (status === "running" || status === "queued") {
      renderBtn.disabled = true;
      renderBtn.classList.remove("pulse");
      renderBtn.replaceChildren(h("span", { class: "spinner" }), status === "queued" ? "En file…" : `Rendu ${Math.round(pct)} %`);
    } else {
      renderBtn.disabled = false;
      renderBtn.replaceChildren(icon("refresh"), "Re-rendre");
    }
  }

  // ----- onglet Découpe -----
  const sIn = h("input", { class: "input time", "aria-label": "Début" });
  const eIn = h("input", { class: "input time", "aria-label": "Fin" });
  const lenEl = h("span", { class: "muted" });
  const tl = new Timeline({
    data: tlData, start: clip.start, end: clip.end,
    onChange: (s, e, final) => {
      player.setRange(s, e);
      syncInputs(s, e);
      if (final) { queue({ start: s, end: e }); save.flush(); }
    },
    onSeek: (t) => { if (t >= clip.start && t <= clip.end) player.seekAbs(t); },
  });
  function syncInputs(s, e) {
    sIn.value = fmtTime(s, 1);
    eIn.value = fmtTime(e, 1);
    lenEl.textContent = `${(e - s).toFixed(1)} s`;
  }
  const fromInputs = () => {
    const s = parseTime(sIn.value);
    const e = parseTime(eIn.value);
    if (isNaN(s) || isNaN(e) || e - s < 1 || e - s > 180) { toast("Début / fin invalides (1 s à 3 min).", "error"); syncInputs(clip.start, clip.end); return; }
    tl.setRange(s, e);
    player.setRange(s, e);
    syncInputs(s, e);
    queue({ start: s, end: e });
    save.flush();
  };
  sIn.addEventListener("change", fromInputs);
  eIn.addEventListener("change", fromInputs);
  const nudge = (which, d) => () => {
    let { start: s, end: e } = clip;
    if (which === "s") s = Math.max(0, s + d); else e = Math.min(project.duration, e + d);
    if (e - s < 1) return;
    tl.setRange(s, e); player.setRange(s, e); syncInputs(s, e); queue({ start: s, end: e });
  };
  syncInputs(clip.start, clip.end);

  const layoutBox = h("div", { class: "layout-choices" });
  const fcBtn = h("button", { class: "btn sm" }, icon("scan"), "Régler la zone facecam");
  fcBtn.onclick = () => openFacecamEditor(project, clip, async (fc) => {
    project.facecam = fc;
    clip = await api.clip(cid);
    player.update(clip);
    refreshHead();
  });
  function renderLayouts() {
    layoutBox.replaceChildren(...LAYOUTS.map(([id, label, desc, parts]) => {
      const mini = h("div", { class: "mini" }, parts.map(([top, hh, col]) => h("i", { style: { top, height: hh, background: col } })));
      const btn = h("button", { class: "choice" + (clip.geometry.layout === id ? " active" : ""), type: "button" }, mini, h("b", {}, label), h("span", { class: "desc" }, desc));
      btn.onclick = async () => {
        if (id === "facecam" && !(clip.facecam && clip.facecam.rect)) { toast("Aucune facecam détectée : définis la zone d'abord.", "info"); fcBtn.click(); return; }
        queue({ layout: id });
        await save.flush();
        if (id === "track" && !clip.track) {
          toast("Calcul du suivi…", "info", 2000);
          try { const r = await api.track(cid); clip.track = r.points; player.update(clip); } catch (e) { toast(e.message, "error"); }
        }
        renderLayouts();
      };
      return btn;
    }));
  }
  renderLayouts();

  const cutTab = h("div", { class: "stack" },
    h("h3", {}, "Début et fin"),
    tl.el,
    h("div", { class: "tl-bar" },
      h("span", { class: "muted" }, "Début"), h("button", { class: "btn sm icon", title: "-0,5 s", onclick: nudge("s", -0.5) }, "−"), sIn, h("button", { class: "btn sm icon", title: "+0,5 s", onclick: nudge("s", 0.5) }, "+"),
      h("span", { class: "muted", style: { marginLeft: "10px" } }, "Fin"), h("button", { class: "btn sm icon", title: "-0,5 s", onclick: nudge("e", -0.5) }, "−"), eIn, h("button", { class: "btn sm icon", title: "+0,5 s", onclick: nudge("e", 0.5) }, "+"),
      lenEl, h("span", { class: "grow" }),
      h("button", { class: "btn sm icon", title: "Dézoomer", onclick: () => tl.zoom(1.6) }, icon("zoomout")),
      h("button", { class: "btn sm icon", title: "Zoomer (Ctrl + molette)", onclick: () => tl.zoom(0.6) }, icon("zoomin"))),
    h("p", { class: "hint" }, "Glisse les poignées : les bornes s'accrochent aux mots. Clique dans la timeline pour te placer."),
    h("div", { class: "row", style: { marginTop: "10px" } }, h("h3", { class: "grow" }, "Layout"), fcBtn),
    layoutBox);

  // ----- onglet Sous-titres -----
  const wordsBox = h("div", { class: "words" });
  const wIn = h("input", { class: "input", placeholder: "Texte du mot", "aria-label": "Texte du mot" });
  const kwBtn = h("button", { class: "btn sm" }, icon("star"), "Mot-clé");
  const delWBtn = h("button", { class: "btn sm" }, icon("trash"), "Masquer");
  const editBar = h("div", { class: "word-edit", hidden: true }, wIn, kwBtn, delWBtn,
    h("span", { class: "hint" }, h("span", { class: "kbd" }, "Entrée"), " valide · ", h("span", { class: "kbd" }, "Tab"), " mot suivant"));
  let selected = -1;
  const visibleWords = () => (clip.words || []).filter((w) => w.e > clip.start && w.s < clip.end);
  function renderWords() {
    const ws = visibleWords();
    if (!ws.length) {
      wordsBox.replaceChildren(h("p", { class: "muted" }, "Aucune parole dans ce clip (gameplay sans voix) : pas de sous-titres."));
      editBar.hidden = true;
      return;
    }
    wordsBox.replaceChildren(...ws.map((w, idx) => {
      const el = h("span", { class: "word" + (w.kw ? " kw" : "") + (w.del ? " del" : "") + (idx === selected ? " sel" : ""), "data-idx": idx, title: fmtTime(w.s - clip.start, 1) }, w.t);
      el.onclick = () => selectWord(idx);
      return el;
    }));
  }
  function selectWord(idx) {
    const ws = visibleWords();
    if (idx < 0 || idx >= ws.length) return;
    selected = idx;
    const w = ws[idx];
    editBar.hidden = false;
    wIn.value = w.t;
    kwBtn.classList.toggle("primary", !!w.kw);
    delWBtn.classList.toggle("primary", !!w.del);
    renderWords();
    player.seekAbs(Math.max(clip.start, w.s - 0.05));
    wIn.focus();
    wIn.select();
  }
  const pushWords = () => { queue({ words: clip.words }); };
  wIn.addEventListener("input", () => { const w = visibleWords()[selected]; if (w) { w.t = wIn.value; pushWords(); const el = wordsBox.querySelector(`[data-idx="${selected}"]`); if (el) el.textContent = wIn.value; } });
  wIn.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); save.flush(); }
    if (e.key === "Tab") { e.preventDefault(); selectWord(selected + (e.shiftKey ? -1 : 1)); }
  });
  kwBtn.onclick = () => { const w = visibleWords()[selected]; if (w) { w.kw = !w.kw; kwBtn.classList.toggle("primary", w.kw); pushWords(); renderWords(); } };
  delWBtn.onclick = () => { const w = visibleWords()[selected]; if (w) { w.del = !w.del; delWBtn.classList.toggle("primary", !!w.del); pushWords(); renderWords(); } };
  const subsTab = h("div", { class: "stack" },
    h("p", { class: "hint" }, "Clique un mot pour corriger son texte, le mettre en couleur (mot-clé) ou le masquer. Le mot prononcé est entouré en bleu pendant la lecture."),
    editBar, wordsBox);

  // ----- onglet Style -----
  const presetBox = h("div", { class: "preset-choices" });
  function renderPresets() {
    presetBox.replaceChildren(...cfg.presets.map((p) => {
      const fam = `AC-${p.font_file.replace(/\.[^.]+$/, "")}`;
      const sample = h("div", { class: "preset-sample", style: { fontFamily: `"${fam}"` } },
        h("span", { style: { color: p.color, WebkitTextStroke: `${Math.max(1, p.outline / 3)}px ${p.outline_color}`, paintOrder: "stroke fill" } }, p.uppercase ? "TROP " : "Trop "),
        h("span", { style: { color: p.active_color, WebkitTextStroke: `${Math.max(1, p.outline / 3)}px ${p.outline_color}`, paintOrder: "stroke fill" } }, p.uppercase ? "FORT" : "fort"));
      sample.style.display = "flex"; sample.style.gap = "6px"; sample.style.alignItems = "center"; sample.style.justifyContent = "center";
      const btn = h("button", { class: "choice" + (clip.captions.preset_id === p.id ? " active" : ""), type: "button" }, sample, h("b", {}, p.label || p.id));
      btn.onclick = async () => { queue({ preset: p.id }); await save.flush(); renderPresets(); };
      return btn;
    }));
  }
  renderPresets();
  const hookIn = h("input", { class: "input", value: clip.hook || "", maxlength: 90, placeholder: "Titre affiché pendant 3 s" });
  const hookChk = h("input", { type: "checkbox", checked: !!clip.show_hook });
  hookIn.addEventListener("input", () => queue({ hook: hookIn.value }));
  hookChk.addEventListener("change", () => { queue({ show_hook: hookChk.checked }); save.flush(); });
  const styleTab = h("div", { class: "stack" },
    h("h3", {}, "Style des sous-titres"), presetBox,
    h("h3", { style: { marginTop: "12px" } }, "Titre hook"),
    h("label", { class: "switch" }, hookChk, "Afficher le titre en haut pendant les 3 premières secondes"),
    hookIn);

  // ----- onglet Publication -----
  const titleIn = h("input", { class: "input", value: clip.title || "", maxlength: 120 });
  const descIn = h("textarea", { class: "input", rows: 4 }, clip.description || "");
  const tagsIn = h("input", { class: "input", value: (clip.hashtags || []).join(" ") });
  titleIn.addEventListener("input", () => queue({ title: titleIn.value }));
  descIn.addEventListener("input", () => queue({ description: descIn.value }));
  tagsIn.addEventListener("change", () => queue({ hashtags: tagsIn.value }));
  const copyBtn = (getter) => h("button", { class: "btn icon", title: "Copier", onclick: () => copyText(getter()) }, icon("copy"));
  const pubTab = h("div", { class: "stack" },
    h("label", { class: "field" }, "Titre (YouTube Shorts)", h("div", { class: "meta-field" }, titleIn, copyBtn(() => titleIn.value))),
    h("label", { class: "field" }, "Description (TikTok / Reels)", h("div", { class: "meta-field" }, descIn, copyBtn(() => descIn.value))),
    h("label", { class: "field" }, "Hashtags", h("div", { class: "meta-field" }, tagsIn, copyBtn(() => tagsIn.value))),
    h("div", {}, h("button", { class: "btn", onclick: () => copyText(`${descIn.value}\n\n${tagsIn.value}`.trim()) }, icon("copy"), "Copier description + hashtags")),
    clip.reason ? h("div", { class: "banner info", style: { marginTop: "8px" } }, h("div", { class: "grow" }, h("b", {}, "Pourquoi ce moment ? "), clip.reason)) : null);

  // ----- onglets -----
  const tabs = [["Découpe", cutTab], ["Sous-titres", subsTab], ["Style", styleTab], ["Publication", pubTab]];
  const tabBtns = tabs.map(([label]) => h("button", { type: "button" }, label));
  const panel = h("div");
  const showTab = (i) => {
    tabBtns.forEach((b, j) => b.classList.toggle("active", i === j));
    panel.replaceChildren(tabs[i][1]);
    if (i === 0) requestAnimationFrame(() => tl.draw());
  };
  tabBtns.forEach((b, i) => { b.onclick = () => showTab(i); });

  root.append(
    h("div", { class: "page-head" },
      h("a", { class: "btn ghost", href: `#/project/${pid}` }, icon("back"), "Projet"),
      h("div", { class: "grow" }, hookTitle, metaLine),
      h("div", { class: "row" }, delBtn, dlBtn, renderBtn)),
    h("div", { class: "editor" },
      player.el,
      h("div", { class: "card" }, h("div", { class: "tabs panel-tabs" }, tabBtns), panel)));
  showTab(0);
  refreshHead();
  renderWords();

  // ----- lecture : mot courant -----
  let lastNow = null;
  function onTime(abs) {
    tl.setPlayhead(abs);
    const ws = visibleWords();
    const idx = ws.findIndex((w) => abs >= w.s && abs < w.e);
    if (idx !== lastNow) {
      wordsBox.querySelectorAll(".word.now").forEach((el) => el.classList.remove("now"));
      if (idx >= 0) { const el = wordsBox.querySelector(`[data-idx="${idx}"]`); if (el) el.classList.add("now"); }
      lastNow = idx;
    }
  }

  const onKey = (e) => {
    if (e.code !== "Space" || ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(document.activeElement.tagName)) return;
    e.preventDefault();
    player.toggle();
  };
  document.addEventListener("keydown", onKey);

  // ----- suivi du rendu (SSE) -----
  es = new EventSource(`/api/projects/${pid}/events`);
  es.addEventListener("state", async (ev) => {
    const st = JSON.parse(ev.data);
    const job = [...(st.jobs || [])].reverse().find((j) => j.clip_id === cid);
    if (!job) return;
    if (job.status === "running" || job.status === "queued") {
      renderJob = job;
      setRenderState(job.status, job.pct || 0);
    } else if (renderJob && renderJob.id === job.id) {
      renderJob = null;
      setRenderState(job.status);
      if (job.status === "done") {
        clip = await api.clip(cid);
        player.update(clip);
        refreshHead();
        hookIn.value = clip.hook || "";
        titleIn.value = clip.title || "";
        descIn.value = clip.description || "";
        tagsIn.value = (clip.hashtags || []).join(" ");
        renderWords();
        toast("Clip rendu ✓", "ok");
      } else if (job.status === "error") toast("Échec du rendu : " + (job.error || ""), "error", 8000);
    }
  });

  return () => {
    save.flush();
    es && es.close();
    document.removeEventListener("keydown", onKey);
    player.destroy();
    tl.destroy();
  };
}
