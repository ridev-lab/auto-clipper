// Client de l'API locale.

async function req(method, url, body, opts = {}) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    if (body instanceof Blob || body instanceof ArrayBuffer) {
      init.body = body;
      init.headers["Content-Type"] = "application/octet-stream";
    } else {
      init.body = JSON.stringify(body);
      init.headers["Content-Type"] = "application/json";
    }
  }
  if (opts.signal) init.signal = opts.signal;
  let res;
  try {
    res = await fetch(url, init);
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new Error("Le serveur ne répond pas. Est-il lancé (start.bat) ?");
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) {
    const err = new Error((data && data.detail) || `Erreur ${res.status}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

export const api = {
  status: (refresh) => req("GET", "/api/status" + (refresh ? "?refresh=1" : "")),
  config: () => req("GET", "/api/config"),
  ytInfo: (url, signal) => req("GET", "/api/youtube/info?url=" + encodeURIComponent(url), undefined, { signal }),
  uploadInit: (filename, size) => req("POST", "/api/uploads", { filename, size }),
  uploadStatus: (id) => req("GET", `/api/uploads/${id}`),
  uploadChunk: (id, offset, blob, signal) => req("PUT", `/api/uploads/${id}?offset=${offset}`, blob, { signal }),
  uploadDelete: (id) => req("DELETE", `/api/uploads/${id}`),
  createProject: (body) => req("POST", "/api/projects", body),
  projects: () => req("GET", "/api/projects"),
  project: (id) => req("GET", `/api/projects/${id}`),
  deleteProject: (id) => req("DELETE", `/api/projects/${id}`),
  resume: (id) => req("POST", `/api/projects/${id}/resume`),
  cancelProject: (id) => req("POST", `/api/projects/${id}/cancel`),
  cancelJob: (id) => req("POST", `/api/jobs/${id}/cancel`),
  timeline: (id) => req("GET", `/api/projects/${id}/timeline`),
  setFacecam: (id, body) => req("PUT", `/api/projects/${id}/facecam`, body),
  addClip: (id, body) => req("POST", `/api/projects/${id}/clips`, body),
  renderAll: (id) => req("POST", `/api/projects/${id}/render-all`),
  clip: (id) => req("GET", `/api/clips/${id}`),
  patchClip: (id, body) => req("PATCH", `/api/clips/${id}`, body),
  renderClip: (id) => req("POST", `/api/clips/${id}/render`),
  deleteClip: (id) => req("DELETE", `/api/clips/${id}`),
  track: (id) => req("GET", `/api/clips/${id}/track`),
};

let configCache = null;
export async function getConfig() {
  if (!configCache) configCache = await api.config();
  return configCache;
}
