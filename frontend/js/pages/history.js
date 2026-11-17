// Historique des projets.
import { api } from "../api.js";
import { STATUS_LABEL, confirmDialog, fmtBytes, fmtDate, fmtTime, h, icon, toast } from "../ui.js";

export async function renderHistory(root) {
  const list = h("div", { class: "hist" });
  const totalEl = h("span", { class: "muted small" });
  root.append(
    h("div", { class: "page-head" }, h("div", { class: "grow" }, h("h1", {}, "Historique"), h("div", { class: "sub" }, totalEl)),
      h("a", { class: "btn primary", href: "#/" }, icon("plus"), "Nouveau projet")),
    list);

  async function load() {
    let projects;
    try { projects = await api.projects(); } catch (e) { list.replaceChildren(h("div", { class: "banner error" }, e.message)); return; }
    const total = projects.reduce((a, p) => a + (p.disk || 0), 0);
    totalEl.textContent = projects.length ? `${projects.length} projet(s) · ${fmtBytes(total)} sur le disque` : "";
    if (!projects.length) {
      list.replaceChildren(h("div", { class: "card empty" }, icon("history"), h("div", {}, "Aucun projet pour l'instant."), h("a", { class: "btn primary", href: "#/", style: { marginTop: "14px" } }, "Créer mon premier projet")));
      return;
    }
    list.replaceChildren(...projects.map((p) => {
      const open = () => { location.hash = `#/project/${p.id}`; };
      const th = h("div", { class: "th", style: p.thumb_url ? { backgroundImage: `url("${p.thumb_url}")` } : {} }, p.thumb_url ? null : icon(p.source_type === "youtube" ? "youtube" : "film"));
      const del = h("button", { class: "btn sm ghost danger", title: "Supprimer" }, icon("trash"));
      del.onclick = async (e) => {
        e.stopPropagation();
        if (!(await confirmDialog("Supprimer ce projet ?", `« ${p.title || p.source_name} » : vidéo source, analyses et ${p.clip_count} clip(s) seront supprimés du disque (${fmtBytes(p.disk)}).`, "Supprimer", true))) return;
        try { await api.deleteProject(p.id); toast("Projet supprimé", "ok"); load(); } catch (err) { toast(err.message, "error"); }
      };
      return h("div", { class: "hist-row" }, th,
        h("div", { style: { minWidth: 0, cursor: "pointer" }, onclick: open },
          h("div", { class: "title" }, p.title || p.source_name || p.id),
          h("div", { class: "info" },
            h("span", { class: `chip ${p.status}` }, STATUS_LABEL[p.status] || p.status),
            h("span", {}, p.source_type === "youtube" ? "YouTube" : "Fichier"),
            h("span", {}, fmtDate(p.created_at)),
            p.duration ? h("span", {}, fmtTime(p.duration)) : null,
            h("span", {}, `${p.clip_count} clip(s)`),
            h("span", {}, fmtBytes(p.disk)))),
        h("div", { class: "row" }, h("button", { class: "btn sm", onclick: open }, "Ouvrir"), del));
    }));
  }
  await load();
}
