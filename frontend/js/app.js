// Point d'entrée : routeur par hash + voyants d'état.
import { api } from "./api.js";
import { h } from "./ui.js";
import { renderNew } from "./pages/new.js";
import { renderProject } from "./pages/project.js";
import { renderEditor } from "./pages/editor.js";
import { renderHistory } from "./pages/history.js";

const app = document.getElementById("app");
let cleanup = null;

const routes = [
  [/^#?\/?$/, () => renderNew(app), "new"],
  [/^#\/history$/, () => renderHistory(app), "history"],
  [/^#\/project\/([\w]+)$/, (m) => renderProject(app, m[1]), ""],
  [/^#\/project\/([\w]+)\/clip\/([\w]+)$/, (m) => renderEditor(app, m[1], m[2]), ""],
];

async function route() {
  if (cleanup) { try { cleanup(); } catch { /* rien */ } cleanup = null; }
  app.replaceChildren();
  const hash = location.hash || "#/";
  for (const [re, fn, nav] of routes) {
    const m = hash.match(re);
    if (m) {
      document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === nav));
      window.scrollTo(0, 0);
      try {
        cleanup = (await fn(m)) || null;
      } catch (e) {
        app.replaceChildren(h("div", { class: "banner error" }, e.message || String(e)));
      }
      return;
    }
  }
  location.hash = "#/";
}

window.addEventListener("hashchange", route);
route();

// ---------------------------------------------------------------------------
// Voyants d'état (GPU, ffmpeg, Ollama, yt-dlp)
// ---------------------------------------------------------------------------
const PILLS = [
  ["GPU", ["gpu", "cuda"]],
  ["ffmpeg", ["ffmpeg", "libass", "nvenc"]],
  ["IA", ["ollama"]],
  ["YouTube", ["ytdlp"]],
  ["Whisper", ["whisper"]],
];

async function refreshStatus(force = false) {
  const bar = document.getElementById("status-bar");
  let data;
  try { data = await api.status(force); } catch { bar.replaceChildren(h("span", { class: "pill" }, h("span", { class: "dot error" }), "Serveur hors ligne")); return; }
  const byId = Object.fromEntries(data.checks.map((c) => [c.id, c]));
  const pills = PILLS.map(([label, ids]) => {
    const cs = ids.map((i) => byId[i]).filter(Boolean);
    if (!cs.length) return null;
    const worst = cs.some((c) => c.status === "error") ? "error" : cs.some((c) => c.status === "warn") ? "warn" : "ok";
    return h("span", { class: "pill", tabindex: "0" }, h("span", { class: `dot ${worst}` }), label,
      h("span", { class: "tip" }, cs.map((c) => h("div", {},
        h("b", {}, c.label), " — ", c.detail, c.fix && c.status !== "ok" ? h("div", { class: "fix" }, "→ ", c.fix) : null))));
  });
  if (data.gpu) {
    const pct = Math.round((data.gpu.vram_used / data.gpu.vram_total) * 100);
    pills.push(h("span", { class: "pill", title: data.gpu.name }, `VRAM ${pct} %`));
  }
  if (data.queue) pills.push(h("span", { class: "pill" }, h("span", { class: "spinner", style: { width: "11px", height: "11px" } }), `${data.queue} tâche(s)`));
  bar.replaceChildren(...pills.filter(Boolean));
}
refreshStatus();
setInterval(refreshStatus, 20000);
window.refreshStatus = refreshStatus;
