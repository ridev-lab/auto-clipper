// Page projet : progression en temps réel (SSE) + grille des clips.
import { api, getConfig } from "../api.js";
import { openManualClip } from "../components/manual-clip.js";
import { STATUS_LABEL, confirmDialog, fmtTime, h, icon, scoreClass, toast } from "../ui.js";

export async function renderProject(root, pid) {
  const cfg = await getConfig();
  let project = await api.project(pid);
  let es = null;
  let lastRev = null;
  let liveJobs = [];
  const pctHistory = [];

  // ----- en-tête -----
  const titleEl = h("h1");
  const subEl = h("div", { class: "sub" });
  const statusChip = h("span", { class: "chip" });
  const exportBtn = h("a", { class: "btn", href: `/api/projects/${pid}/export.zip`, download: "" }, icon("zip"), "Tout exporter");
  const manualBtn = h("button", { class: "btn" }, icon("plus"), "Clip manuel");
  const renderAllBtn = h("button", { class: "btn", hidden: true }, icon("refresh"), "Re-rendre les clips modifiés");
  const delBtn = h("button", { class: "btn ghost danger", title: "Supprimer le projet" }, icon("trash"));
  manualBtn.onclick = () => openManualClip(project, (clip) => { location.hash = `#/project/${pid}/clip/${clip.id}`; });
  renderAllBtn.onclick = async () => { try { await api.renderAll(pid); toast("Rendus ajoutés à la file", "ok"); } catch (e) { toast(e.message, "error"); } };
  delBtn.onclick = async () => {
    if (!(await confirmDialog("Supprimer ce projet ?", "La vidéo source, les analyses et tous les clips rendus seront supprimés du disque. Action irréversible.", "Supprimer", true))) return;
    try { await api.deleteProject(pid); toast("Projet supprimé", "ok"); location.hash = "#/history"; } catch (e) { toast(e.message, "error"); }
  };

  // ----- progression -----
  const procCard = h("div", { class: "card", hidden: true });
  const stepsEl = h("ol", { class: "steps" });
  const bigPct = h("div", { class: "big-pct" }, "0 %");
  const bar = h("i");
  const msgEl = h("div", { class: "muted" });
  const etaEl = h("div", { class: "muted small" });
  const logsEl = h("div", { class: "logs", role: "log" });
  const bannerEl = h("div");
  const cancelBtn = h("button", { class: "btn danger" }, icon("x"), "Annuler");
  const resumeBtn = h("button", { class: "btn primary", hidden: true }, icon("refresh"), "Reprendre");
  const logToggle = h("button", { class: "btn sm ghost" }, "Masquer les logs");
  logToggle.onclick = () => { logsEl.hidden = !logsEl.hidden; logToggle.textContent = logsEl.hidden ? "Afficher les logs" : "Masquer les logs"; };
  cancelBtn.onclick = async () => {
    if (!(await confirmDialog("Annuler le traitement ?", "Les étapes déjà terminées restent en cache : tu pourras reprendre plus tard.", "Annuler le traitement", true))) return;
    try { await api.cancelProject(pid); } catch (e) { toast(e.message, "error"); }
  };
  resumeBtn.onclick = async () => { try { await api.resume(pid); bannerEl.replaceChildren(); } catch (e) { toast(e.message, "error"); } };
  procCard.append(
    bannerEl,
    h("div", { class: "proc" },
      h("div", {}, stepsEl),
      h("div", {},
        h("div", { class: "row" }, h("div", { class: "grow" }, bigPct, msgEl, etaEl), cancelBtn, resumeBtn),
        h("div", { class: "progress big", style: { marginTop: "12px" } }, bar),
        h("div", { class: "row", style: { marginTop: "12px" } }, h("h3", { class: "grow" }, "Journal"), logToggle),
        logsEl)));
  const stepItems = {};
  for (const s of cfg.steps) {
    const li = h("li", {}, h("span", { class: "st" }), h("span", {}, s.label), h("span", { class: "pct" }));
    stepItems[s.id] = li;
    stepsEl.append(li);
  }

  // ----- clips -----
  const grid = h("div", { class: "clips-grid" });
  const clipsHead = h("div", { class: "section-title" }, h("h2", { class: "grow" }, "Clips"), h("span", { class: "muted small" }));

  root.append(
    h("div", { class: "page-head" },
      h("div", { class: "grow" }, titleEl, subEl),
      h("div", { class: "row" }, renderAllBtn, manualBtn, exportBtn, delBtn)),
    procCard,
    h("div", { class: "card", style: { marginTop: "16px" } }, clipsHead, grid),
  );

  function renderHead() {
    titleEl.textContent = project.title || project.source_name || "Projet";
    statusChip.className = `chip ${project.status}`;
    statusChip.textContent = STATUS_LABEL[project.status] || project.status;
    const src = project.source_type === "youtube"
      ? h("a", { href: project.source_url, target: "_blank", rel: "noopener" }, icon("youtube"), " YouTube")
      : h("span", {}, icon("file"), " ", project.source_name);
    subEl.replaceChildren(...[statusChip, src,
      project.duration ? h("span", {}, fmtTime(project.duration)) : null,
      project.width ? h("span", {}, `${project.width}×${project.height} · ${Math.round(project.fps)} i/s`) : null,
      project.language ? h("span", {}, `langue : ${project.language}`) : null].filter(Boolean));
    const hasFiles = (project.clips || []).some((c) => c.file);
    exportBtn.toggleAttribute("hidden", !hasFiles);
    manualBtn.disabled = !project.preview_url;
    renderAllBtn.hidden = !(project.clips || []).some((c) => c.dirty && c.file) || project.status === "running";
  }

  function jobForClip(cid) {
    return liveJobs.find((j) => j.clip_id === cid && (j.status === "running" || j.status === "queued"));
  }

  function renderClips() {
    const clips = (project.clips || []).slice().sort((a, b) => (b.score - a.score) || (a.start - b.start));
    clipsHead.lastChild.textContent = clips.length ? `${clips.length} clip(s) — triés par score` : "";
    if (!clips.length) {
      const running = ["queued", "running"].includes(project.status);
      grid.replaceChildren(h("div", { class: "empty", style: { gridColumn: "1/-1" } }, icon("film"),
        h("div", {}, running ? "Les clips apparaîtront ici à la fin de l'analyse." : "Aucun clip pour l'instant.")));
      return;
    }
    grid.replaceChildren(...clips.map(clipCard));
  }

  function clipCard(c) {
    const job = jobForClip(c.id);
    const open = () => { location.hash = `#/project/${pid}/clip/${c.id}`; };
    const thumb = h("div", { class: "clip-thumb", onclick: open, title: "Ouvrir l'éditeur" });
    if (c.thumb_url) thumb.append(h("img", { src: c.thumb_url, alt: "", loading: "lazy" }));
    else thumb.append(h("div", { class: "placeholder" }, job ? h("span", { class: "spinner" }) : icon("film")));
    if (c.file_url) {
      let vid = null;
      thumb.addEventListener("mouseenter", () => {
        vid = h("video", { src: c.file_url, muted: true, autoplay: true, loop: true, playsinline: true });
        vid.muted = true;
        thumb.append(vid);
      });
      thumb.addEventListener("mouseleave", () => { vid && vid.remove(); vid = null; });
    }
    thumb.append(h("span", { class: `score ${scoreClass(c.score)}`, title: "Score viral" }, c.manual ? "✎" : c.score));
    thumb.append(h("span", { class: "clip-dur" }, fmtTime(c.end - c.start)));
    if (job) thumb.append(h("span", { class: "clip-state busy" }, job.status === "queued" ? "En file" : `Rendu ${Math.round(job.pct || 0)} %`));
    else if (c.render_status === "error") thumb.append(h("span", { class: "clip-state err" }, "Erreur de rendu"));
    else if (c.dirty && c.file) thumb.append(h("span", { class: "clip-state dirty" }, "Modifié"));
    const sig = c.signals || {};
    const sigEl = h("div", { class: "signals" },
      sig.llm !== undefined ? h("span", { class: "sig" }, "IA ", h("b", {}, Math.round(sig.llm * 100))) : null,
      sig.audio !== undefined ? h("span", { class: "sig" }, "Audio ", h("b", {}, Math.round(sig.audio * 100))) : null,
      sig.motion !== undefined ? h("span", { class: "sig" }, "Action ", h("b", {}, Math.round(sig.motion * 100))) : null,
      sig.scenes !== undefined ? h("span", { class: "sig" }, "Cuts ", h("b", {}, Math.round(sig.scenes * 100))) : null);
    return h("article", { class: "clip-card" }, thumb,
      h("div", { class: "clip-body" },
        h("div", { class: "hook" }, c.hook || c.title || "Sans titre"),
        h("div", { class: "muted small" }, `${fmtTime(c.start)} → ${fmtTime(c.end)}`),
        c.reason ? h("div", { class: "reason" }, c.reason) : null,
        sigEl,
        h("div", { class: "clip-actions" },
          h("button", { class: "btn sm", onclick: open }, icon("edit"), "Éditer"),
          c.file ? h("a", { class: "btn sm", href: `/api/clips/${c.id}/download`, download: "" }, icon("download"), "MP4") : null)));
  }

  function appendLogs(logs, reset) {
    if (reset) logsEl.replaceChildren();
    const atBottom = logsEl.scrollTop + logsEl.clientHeight >= logsEl.scrollHeight - 30;
    for (const l of logs) {
      logsEl.append(h("div", { class: `l-${l.level}` }, h("span", { class: "t" }, l.t), l.msg));
    }
    while (logsEl.childElementCount > 600) logsEl.firstChild.remove();
    if (atBottom || reset) logsEl.scrollTop = logsEl.scrollHeight;
  }

  let logJob = null;
  function onState(st) {
    liveJobs = st.jobs || [];
    project.status = st.project.status;
    project.error = st.project.error;
    if (st.project.title) project.title = st.project.title;
    const proc = [...liveJobs].reverse().find((j) => j.type === "process");
    const showProc = proc && (["queued", "running", "error", "interrupted", "cancelled"].includes(proc.status) || project.status !== "done");
    procCard.hidden = !showProc;
    if (st.log_job !== logJob) { appendLogs(st.logs || [], true); logJob = st.log_job; }
    else if (st.logs && st.logs.length) appendLogs(st.logs, false);
    if (proc) {
      const running = proc.status === "running" || proc.status === "queued";
      const pct = proc.status === "done" ? 100 : proc.pct || 0;
      bigPct.textContent = `${Math.round(pct)} %`;
      bar.style.width = `${pct}%`;
      msgEl.textContent = proc.status === "queued"
        ? (proc.queue_pos ? `En file d'attente (position ${proc.queue_pos}) : démarre dès que la tâche en cours est finie` : "Démarrage…")
        : (proc.message || "");
      cancelBtn.hidden = !running;
      resumeBtn.hidden = running || proc.status === "done";
      // estimation du temps restant
      if (proc.status === "running") {
        const now = Date.now() / 1000;
        pctHistory.push([now, pct]);
        while (pctHistory.length > 2 && now - pctHistory[0][0] > 90) pctHistory.shift();
        const [t0, p0] = pctHistory[0];
        const rate = (pct - p0) / Math.max(1, now - t0);
        etaEl.textContent = rate > 0.01 && pct > 3 ? `Temps restant estimé : ${fmtTime((100 - pct) / rate)}` : "";
      } else etaEl.textContent = "";
      // étapes
      const order = cfg.steps.map((s) => s.id);
      const cur = order.indexOf(proc.step);
      for (const [i, id] of order.entries()) {
        const li = stepItems[id];
        li.className = "";
        li.querySelector(".st").textContent = "";
        li.querySelector(".pct").textContent = "";
        if (proc.status === "done" || i < cur) { li.className = "done"; li.querySelector(".st").textContent = "✓"; }
        else if (i === cur) {
          if (proc.status === "running") { li.className = "active"; li.querySelector(".pct").textContent = `${Math.round(proc.step_pct || 0)} %`; }
          else if (proc.status === "error") { li.className = "failed"; li.querySelector(".st").textContent = "!"; }
        }
      }
      if (proc.status === "error") bannerEl.replaceChildren(h("div", { class: "banner error" }, h("div", { class: "grow" }, h("b", {}, "Le traitement a échoué. "), proc.error || project.error || "")));
      else if (proc.status === "interrupted") bannerEl.replaceChildren(h("div", { class: "banner warn" }, h("div", { class: "grow" }, "Le traitement a été interrompu (serveur arrêté). Clique sur « Reprendre » : les étapes déjà faites ne sont pas refaites.")));
      else if (proc.status === "cancelled") bannerEl.replaceChildren(h("div", { class: "banner warn" }, h("div", { class: "grow" }, "Traitement annulé. Tu peux le reprendre là où il s'était arrêté.")));
      else bannerEl.replaceChildren();
    }
    renderHead();
    if (st.clips_rev !== lastRev) {
      const first = lastRev === null;
      lastRev = st.clips_rev;
      if (!first) api.project(pid).then((p) => { project = p; renderHead(); renderClips(); }).catch(() => {});
    }
    // la grille n'est redessinée que si l'état d'un rendu change (sinon les
    // aperçus vidéo au survol seraient interrompus)
    const sig = project.status + JSON.stringify(liveJobs.filter((j) => j.clip_id).map((j) => [j.clip_id, j.status, Math.round((j.pct || 0) / 5)]));
    if (sig !== clipJobsSig) { clipJobsSig = sig; renderClips(); }
  }
  let clipJobsSig = "";

  renderHead();
  renderClips();
  es = new EventSource(`/api/projects/${pid}/events`);
  es.addEventListener("state", (e) => onState(JSON.parse(e.data)));
  es.addEventListener("gone", () => { es.close(); toast("Ce projet a été supprimé.", "error"); location.hash = "#/history"; });
  return () => { es && es.close(); };
}
