"""SQLite : projets, jobs, clips. Accès thread-safe (un verrou global)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from clipper.config import db_path

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    created_at REAL,
    updated_at REAL,
    source_type TEXT,
    source_url TEXT,
    source_name TEXT,
    title TEXT,
    status TEXT,
    error TEXT,
    duration REAL,
    width INTEGER,
    height INTEGER,
    fps REAL,
    language TEXT,
    source_file TEXT,
    options TEXT,
    facecam TEXT,
    info TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    type TEXT,
    clip_id TEXT,
    status TEXT,
    step TEXT,
    progress REAL,
    message TEXT,
    error TEXT,
    created_at REAL,
    started_at REAL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    created_at REAL,
    updated_at REAL,
    rank INTEGER,
    manual INTEGER DEFAULT 0,
    start REAL,
    "end" REAL,
    score INTEGER,
    signals TEXT,
    hook TEXT,
    reason TEXT,
    layout TEXT,
    preset TEXT,
    show_hook INTEGER DEFAULT 1,
    words TEXT,
    title TEXT,
    description TEXT,
    hashtags TEXT,
    file TEXT,
    thumb TEXT,
    render_status TEXT,
    rendered_at REAL,
    dirty INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id);
CREATE INDEX IF NOT EXISTS idx_clips_project ON clips(project_id);
"""

JSON_FIELDS = {"options", "facecam", "info", "signals", "words", "hashtags"}


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            p: Path = db_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(p), check_same_thread=False, timeout=30)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
            _conn.commit()
        return _conn


def new_id(n: int = 10) -> str:
    return uuid.uuid4().hex[:n]


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_FIELDS & d.keys():
        if d[k]:
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                d[k] = None
    return d


def _enc(fields: dict) -> dict:
    out = {}
    for k, v in fields.items():
        out[k] = json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS and v is not None else v
    return out


def _insert(table: str, fields: dict) -> None:
    f = _enc(fields)
    cols = ",".join(f'"{k}"' for k in f)
    q = ",".join("?" for _ in f)
    with _lock:
        c = conn()
        c.execute(f"INSERT INTO {table} ({cols}) VALUES ({q})", list(f.values()))
        c.commit()


def _update(table: str, id_: str, fields: dict) -> None:
    if not fields:
        return
    f = _enc(fields)
    sets = ",".join(f'"{k}"=?' for k in f)
    with _lock:
        c = conn()
        c.execute(f"UPDATE {table} SET {sets} WHERE id=?", [*f.values(), id_])
        c.commit()


def _select(q: str, args=()) -> list[dict]:
    with _lock:
        return [_row(r) for r in conn().execute(q, args).fetchall()]


# --- projets ---------------------------------------------------------------

def create_project(**fields) -> dict:
    now = time.time()
    pid = fields.pop("id", None) or new_id()
    _insert("projects", {"id": pid, "created_at": now, "updated_at": now, "status": "queued", **fields})
    return get_project(pid)


def get_project(pid: str) -> dict | None:
    r = _select("SELECT * FROM projects WHERE id=?", (pid,))
    return r[0] if r else None


def update_project(pid: str, **fields) -> None:
    fields["updated_at"] = time.time()
    _update("projects", pid, fields)


def list_projects() -> list[dict]:
    rows = _select(
        "SELECT p.*, (SELECT COUNT(*) FROM clips c WHERE c.project_id=p.id) AS clip_count "
        "FROM projects p ORDER BY created_at DESC"
    )
    return rows


def delete_project(pid: str) -> None:
    with _lock:
        c = conn()
        c.execute("DELETE FROM clips WHERE project_id=?", (pid,))
        c.execute("DELETE FROM jobs WHERE project_id=?", (pid,))
        c.execute("DELETE FROM projects WHERE id=?", (pid,))
        c.commit()


# --- jobs ------------------------------------------------------------------

def create_job(project_id: str, type_: str, clip_id: str | None = None) -> dict:
    jid = new_id(12)
    _insert("jobs", {
        "id": jid, "project_id": project_id, "type": type_, "clip_id": clip_id, "status": "queued",
        "step": "", "progress": 0.0, "message": "En attente", "created_at": time.time(),
    })
    return get_job(jid)


def get_job(jid: str) -> dict | None:
    r = _select("SELECT * FROM jobs WHERE id=?", (jid,))
    return r[0] if r else None


def update_job(jid: str, **fields) -> None:
    _update("jobs", jid, fields)


def next_queued_job() -> dict | None:
    r = _select("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1")
    return r[0] if r else None


def list_jobs(project_id: str | None = None, active_only: bool = False) -> list[dict]:
    q = "SELECT * FROM jobs"
    cond, args = [], []
    if project_id:
        cond.append("project_id=?")
        args.append(project_id)
    if active_only:
        cond.append("status IN ('queued','running')")
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY created_at"
    return _select(q, args)


def mark_interrupted() -> int:
    with _lock:
        c = conn()
        cur = c.execute(
            "UPDATE jobs SET status='interrupted', message='Interrompu (serveur arrêté)' WHERE status='running'"
        )
        c.execute(
            "UPDATE projects SET status='interrupted' WHERE status IN ('running','queued') "
            "AND id NOT IN (SELECT project_id FROM jobs WHERE status='queued')"
        )
        c.commit()
        return cur.rowcount


# --- clips -----------------------------------------------------------------

def create_clip(project_id: str, **fields) -> dict:
    now = time.time()
    cid = fields.pop("id", None) or new_id(8)
    _insert("clips", {"id": cid, "project_id": project_id, "created_at": now, "updated_at": now, **fields})
    return get_clip(cid)


def get_clip(cid: str) -> dict | None:
    r = _select('SELECT * FROM clips WHERE id=?', (cid,))
    return r[0] if r else None


def update_clip(cid: str, **fields) -> None:
    fields["updated_at"] = time.time()
    _update("clips", cid, fields)


def list_clips(project_id: str) -> list[dict]:
    return _select("SELECT * FROM clips WHERE project_id=? ORDER BY score DESC, start", (project_id,))


def delete_clip(cid: str) -> None:
    with _lock:
        c = conn()
        c.execute("DELETE FROM clips WHERE id=?", (cid,))
        c.commit()
