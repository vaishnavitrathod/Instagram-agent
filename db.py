"""SQLite store. Status flow: scheduled -> publishing -> published | failed."""
import sqlite3
from datetime import datetime, timezone

DB_PATH = "instagram_agent.db"


def conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            caption TEXT NOT NULL,
            image_path TEXT NOT NULL,
            status TEXT NOT NULL,
            approved_by TEXT,
            publish_at TEXT NOT NULL,
            thread_id TEXT,
            error TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS style (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            draft TEXT NOT NULL,
            final TEXT NOT NULL,
            edited INTEGER NOT NULL
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            step TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT,
            ms INTEGER
        )"""
    )
    return c


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add_scheduled(platform, caption, image_path, approved_by, publish_at, thread_id):
    c = conn()
    cur = c.execute(
        "INSERT INTO posts (platform, caption, image_path, status, approved_by, publish_at,"
        " thread_id, created_at) VALUES (?, ?, ?, 'scheduled', ?, ?, ?, ?)",
        (platform, caption, image_path, approved_by, publish_at, thread_id, now()),
    )
    c.commit()
    return cur.lastrowid


def due_posts():
    return conn().execute(
        "SELECT * FROM posts WHERE status='scheduled' AND publish_at <= ? ORDER BY publish_at",
        (now(),),
    ).fetchall()


def claim(post_id):
    """Atomic claim so a post can never be published twice."""
    c = conn()
    cur = c.execute("UPDATE posts SET status='publishing' WHERE id=? AND status='scheduled'", (post_id,))
    c.commit()
    return cur.rowcount == 1


def finish(post_id, ok, error=None):
    c = conn()
    c.execute("UPDATE posts SET status=?, error=? WHERE id=?",
              ("published" if ok else "failed", error, post_id))
    c.commit()


def all_posts():
    return conn().execute("SELECT * FROM posts ORDER BY id DESC LIMIT 20").fetchall()


import json as _json


def pack(paths):
    """One image -> plain path (backward compatible). Several -> JSON list."""
    paths = list(paths)
    return paths[0] if len(paths) == 1 else _json.dumps(paths)


def unpack(value):
    return _json.loads(value) if value and value.startswith("[") else [value]


def add_style(draft, final):
    """Remember an approved caption and whether the owner changed the AI draft."""
    c = conn()
    c.execute("INSERT INTO style (created_at, draft, final, edited) VALUES (?,?,?,?)",
              (now(), draft, final, int(draft.strip() != final.strip())))
    c.commit()


def style_examples(limit=6):
    return conn().execute("SELECT * FROM style ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def style_stats():
    total, kept = conn().execute("SELECT COUNT(*), COALESCE(SUM(1 - edited), 0) FROM style").fetchone()
    return total, kept


def style_reset():
    c = conn()
    c.execute("DELETE FROM style")
    c.commit()


# ---- agent activity timeline ----
def log_event(thread_id, step, status, detail="", ms=None):
    """Timeline entry. Must never break the pipeline."""
    if not thread_id:
        return
    try:
        c = conn()
        c.execute("INSERT INTO events (thread_id, ts, step, status, detail, ms) VALUES (?,?,?,?,?,?)",
                  (thread_id, datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                   step, status, detail, ms))
        c.commit()
    except Exception as e:  # noqa: BLE001
        print("timeline skipped:", e)


def log_waiting(thread_id):
    last = conn().execute("SELECT status FROM events WHERE thread_id=? ORDER BY id DESC LIMIT 1",
                          (thread_id,)).fetchone()
    if not last or last["status"] != "waiting":  # avoid duplicates if a paused run is re-invoked
        log_event(thread_id, "approval", "waiting", "Paused for human review")


def log_decision(thread_id, action):
    """Duration = how long the human took since the pause started."""
    row = conn().execute("SELECT ts FROM events WHERE thread_id=? AND status='waiting' ORDER BY id DESC LIMIT 1",
                         (thread_id,)).fetchone()
    ms = None
    if row:
        ms = int((datetime.now(timezone.utc) - datetime.fromisoformat(row["ts"])).total_seconds() * 1000)
    log_event(thread_id, "human", "done", action, ms)


def timeline(thread_id):
    rows = conn().execute("SELECT ts, step, status, detail, ms FROM events WHERE thread_id=? ORDER BY id",
                          (thread_id,)).fetchall()
    ev = [dict(r) for r in rows]
    agent = sum(e["ms"] or 0 for e in ev if e["step"] != "human")
    human = sum(e["ms"] or 0 for e in ev if e["step"] == "human")
    return {"events": ev, "agent_ms": agent, "human_ms": human}