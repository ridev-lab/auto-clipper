"""File de jobs : un seul worker (un seul traitement GPU à la fois),
état persistant en base, progression en mémoire pour le flux SSE."""
from __future__ import annotations

import logging
import threading
import time
import traceback
from collections import deque

from clipper.config import workspace_dir
from clipper.utils import Cancelled, ClipperError, Context

from . import db, tasks

log = logging.getLogger("jobs")


class JobManager:
    def __init__(self):
        self._wake = threading.Event()
        self._lock = threading.RLock()
        self.live: dict[str, dict] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._thread: threading.Thread | None = None
        self.current: str | None = None

    # ------------------------------------------------------------------
    def start(self) -> None:
        n = db.mark_interrupted()
        if n:
            log.warning("%d job(s) interrompu(s) au dernier arrêt", n)
        self._thread = threading.Thread(target=self._loop, name="job-worker", daemon=True)
        self._thread.start()

    def submit(self, project_id: str, type_: str, clip_id: str | None = None) -> dict:
        # pas de doublon : un même rendu déjà en attente est réutilisé
        for j in db.list_jobs(project_id, active_only=True):
            if j["type"] == type_ and j.get("clip_id") == clip_id and j["status"] == "queued":
                return j
        job = db.create_job(project_id, type_, clip_id)
        if type_ == "process":
            db.update_project(project_id, status="queued", error=None)
        self._wake.set()
        return job

    def cancel(self, job_id: str) -> bool:
        job = db.get_job(job_id)
        if not job:
            return False
        if job["status"] == "queued":
            db.update_job(job_id, status="cancelled", message="Annulé", finished_at=time.time())
            if job["type"] == "process":
                db.update_project(job["project_id"], status="cancelled")
            return True
        ev = self._cancel.get(job_id)
        if ev:
            ev.set()
            return True
        return False

    def cancel_project(self, project_id: str, wait: float = 8.0) -> None:
        for j in db.list_jobs(project_id, active_only=True):
            self.cancel(j["id"])
        t0 = time.time()
        while time.time() - t0 < wait:
            cur = self.current
            if not cur or (db.get_job(cur) or {}).get("project_id") != project_id:
                return
            time.sleep(0.2)

    def live_state(self, job_id: str) -> dict | None:
        with self._lock:
            s = self.live.get(job_id)
            return dict(s, logs=list(s["logs"])) if s else None

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        while True:
            try:
                job = db.next_queued_job()
            except Exception:  # base verrouillée…
                log.exception("lecture de la file")
                job = None
            if not job:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            self._run(job)

    def _run(self, job: dict) -> None:
        jid, pid = job["id"], job["project_id"]
        ev = threading.Event()
        self._cancel[jid] = ev
        self.current = jid
        logdir = workspace_dir(pid) / "logs"
        logdir.mkdir(parents=True, exist_ok=True)
        logfile = open(logdir / f"{jid}.log", "a", encoding="utf-8")
        state = {
            "job_id": jid, "type": job["type"], "status": "running", "step": "", "step_pct": 0.0,
            "pct": 0.0, "message": "Démarrage", "logs": deque(maxlen=400), "seq": 0, "started": time.time(),
        }
        with self._lock:
            self.live[jid] = state
        db.update_job(jid, status="running", started_at=time.time(), message="Démarrage")
        last_db = [0.0]

        def on_progress(step: str, pct: float, msg: str):
            with self._lock:
                state["step"], state["step_pct"] = step, round(pct, 1)
                state["pct"] = tasks.overall_progress(job["type"], step, pct)
                if msg:
                    state["message"] = msg
            now = time.time()
            if now - last_db[0] > 1.5:
                last_db[0] = now
                db.update_job(jid, step=step, progress=state["pct"], message=state["message"])

        def on_log(msg: str, level: str):
            line = {"t": time.strftime("%H:%M:%S"), "level": level, "msg": msg}
            with self._lock:
                state["seq"] += 1
                line["seq"] = state["seq"]
                state["logs"].append(line)
            try:
                logfile.write(f"{line['t']} {level.upper():7} {msg}\n")
                logfile.flush()
            except OSError:
                pass

        ctx = Context(pid, on_progress=on_progress, on_log=on_log, cancel_event=ev)
        status, error = "done", None
        try:
            tasks.run_job(ctx, job)
        except Cancelled:
            status = "cancelled"
            on_log("Traitement annulé", "warning")
        except ClipperError as e:
            status, error = "error", str(e)
            on_log(str(e), "error")
        except Exception as e:  # erreur inattendue : trace complète dans le log
            status, error = "error", f"Erreur inattendue : {e}"
            on_log(error, "error")
            for l in traceback.format_exc().splitlines():
                on_log(l, "debug")
            log.exception("job %s", jid)
        finally:
            logfile.close()
            self._cancel.pop(jid, None)
            self.current = None
        with self._lock:
            state["status"] = status
            if status == "done":
                state["pct"] = 100.0
                state["message"] = "Terminé"
            elif status == "cancelled":
                state["message"] = "Annulé"
            else:
                state["message"] = error or "Erreur"
        db.update_job(jid, status=status, error=error, finished_at=time.time(), progress=state["pct"], message=state["message"])
        if job["type"] == "process" and status != "done":
            db.update_project(pid, status=status if status != "done" else "done", error=error)
        if job["type"] == "render" and status != "done" and job.get("clip_id"):
            c = db.get_clip(job["clip_id"])
            if c:
                db.update_clip(job["clip_id"], render_status="error" if status == "error" else ("done" if c.get("file") else "pending"))


manager = JobManager()
