// Upload en morceaux de 8 Mo, reprenable (même fichier resélectionné =
// reprise là où il s'était arrêté).
import { api } from "./api.js";
import { store } from "./ui.js";

const CHUNK = 8 * 1024 * 1024;

function fileKey(f) { return `upload:${f.name}:${f.size}:${f.lastModified}`; }

export class Uploader {
  constructor(file, { onProgress, onDone, onError }) {
    this.file = file;
    this.onProgress = onProgress;
    this.onDone = onDone;
    this.onError = onError;
    this.abort = new AbortController();
    this.id = null;
    this.stopped = false;
  }

  async start() {
    const f = this.file;
    try {
      let offset = 0;
      const saved = store(fileKey(f));
      if (saved && saved.id) {
        try {
          const st = await api.uploadStatus(saved.id);
          if (st.size === f.size) { this.id = saved.id; offset = st.received; }
        } catch { /* upload expiré : on recommence */ }
      }
      if (!this.id) {
        const init = await api.uploadInit(f.name, f.size);
        this.id = init.id;
        store(fileKey(f), { id: this.id });
      }
      const t0 = performance.now();
      const startOffset = offset;
      while (offset < f.size) {
        if (this.stopped) return;
        const blob = f.slice(offset, Math.min(f.size, offset + CHUNK));
        let tries = 0;
        for (;;) {
          try {
            const r = await api.uploadChunk(this.id, offset, blob, this.abort.signal);
            offset = r.received;
            break;
          } catch (e) {
            if (e.name === "AbortError" || this.stopped) return;
            if (e.status === 409 && e.data && typeof e.data.received === "number") { offset = e.data.received; break; }
            if (++tries >= 4) throw e;
            await new Promise((r) => setTimeout(r, 800 * tries));
          }
        }
        const secs = (performance.now() - t0) / 1000;
        const speed = (offset - startOffset) / Math.max(0.1, secs);
        this.onProgress && this.onProgress({ loaded: offset, total: f.size, speed, eta: (f.size - offset) / Math.max(1, speed) });
      }
      store(fileKey(f), null);
      this.onDone && this.onDone(this.id);
    } catch (e) {
      if (!this.stopped) this.onError && this.onError(e);
    }
  }

  cancel() {
    this.stopped = true;
    this.abort.abort();
    if (this.id) {
      store(fileKey(this.file), null);
      api.uploadDelete(this.id).catch(() => {});
    }
  }
}
