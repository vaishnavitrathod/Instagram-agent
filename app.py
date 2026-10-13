"""Web dashboard: python app.py -> http://127.0.0.1:5000
Reuses the same LangGraph agent. Runs the publish worker in a background thread."""
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()
import hmac  # noqa: E402
import secrets  # noqa: E402
from flask import (Flask, Response, abort, jsonify, redirect, request,  # noqa: E402
                   send_from_directory, session)
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

import db  # noqa: E402
import graph as G  # noqa: E402
import images  # noqa: E402
import publisher  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
IMG = os.path.abspath(images.IMG_DIR)
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
app = Flask(__name__, static_folder=None)
PASSWORD = os.getenv("APP_PASSWORD", "")
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    MAX_CONTENT_LENGTH=60 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)
FAILS = []  # timestamps of recent failed logins (global lockout, simple and safe)
PUBLIC_ASSETS = {"box.jpg", "cat.webp", "cat-still.png"}  # the login page needs these before anyone is logged in
LOGIN_HTML = """<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Log in</title>
<link rel=preconnect href="https://fonts.googleapis.com"><link rel=preconnect href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,700..800&family=Figtree:wght@400..700&display=swap" rel=stylesheet>
<style>
:root{color-scheme:light dark;--bg:#fffcf6;--ink:#2a2140;--mute:#6d6287;--lav:#b9a3f5;--lav-d:#6b52c9;--blush:#f2a7bc;--bad:#8a2c1f}
@media(prefers-color-scheme:dark){:root{--bg:#150f1f;--ink:#f3edff;--mute:#a99ec5;--bad:#ffb4a8}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;color:var(--ink);
font:15px/1.5 Figtree,system-ui,sans-serif;
background:radial-gradient(60% 50% at 12% 8%,#f2a7bc40,transparent 70%),radial-gradient(55% 50% at 92% 92%,#b9a3f54d,transparent 70%),var(--bg)}
.wrap{width:clamp(340px,92vw,460px);animation:up .5s ease both}
@keyframes up{from{opacity:0;transform:translateY(14px)}}
.cat{display:block;width:52%;margin:0 6% -3px auto;position:relative;z-index:1}
.cat img{display:block;width:100%;height:auto}
.win{position:relative;border-radius:14px;overflow:hidden;box-shadow:0 22px 44px -22px #b9a3f5cc}
.frame{display:block;width:100%;height:auto}
.in{position:absolute;left:6.5%;top:26.8%;width:80%;height:56.3%;padding:0 7%;display:flex;flex-direction:column;justify-content:center;gap:9px}
h1{margin:0 0 2px;font:800 23px/1.1 'Bricolage Grotesque',Figtree,system-ui,sans-serif;letter-spacing:-.02em;color:#2a2140;text-align:center}
input,button{font:inherit;width:100%;height:41px;border-radius:99px;padding:0 16px}
input{border:1.5px solid #e1d6fa;background:#fff;color:#2a2140;text-align:center}
input::placeholder{color:#6d6287}
input:focus-visible,button:focus-visible{outline:3px solid var(--lav-d);outline-offset:2px}
button{border:0;background:var(--lav);color:#2a2140;font-weight:700;cursor:pointer;transition:transform .12s,filter .15s}
button:hover{filter:brightness(1.07)}button:active{transform:scale(.97)}
.e{min-height:1.6em;margin:10px 0 0;text-align:center;color:var(--bad);font-weight:600}
.s{margin:0;text-align:center;color:var(--mute);font-size:14px}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>
<main class=wrap>
<picture class=cat><source media="(prefers-reduced-motion: reduce)" srcset="/assets/cat-still.png"><img src="/assets/cat.webp" alt="" width=300 height=169></picture>
<div class=win><img class=frame src="/assets/box.jpg" alt="" width=550 height=433>
<form class=in method=post><h1>Instagram Agent</h1>
<input type=password name=password placeholder=Password aria-label=Password autocomplete=current-password autofocus required>
<button>Log in</button></form></div>
<p class=e role=alert>{err}</p>
<p class=s>Nothing is posted until you say so.</p>
</main></html>"""
agent = G.build(SqliteSaver(sqlite3.connect("instagram_checkpoints.db", check_same_thread=False)))


def init():
    c = db.conn()
    c.execute("""CREATE TABLE IF NOT EXISTS runs (thread_id TEXT PRIMARY KEY, topic TEXT,
                 status TEXT, error TEXT, post_id INTEGER, created_at TEXT)""")
    c.execute("UPDATE runs SET status='error', error='Server restarted while working' WHERE status='working'")
    c.commit()


def set_run(tid, status, error=None, post_id=None):
    c = db.conn()
    c.execute("UPDATE runs SET status=?, error=?, post_id=? WHERE thread_id=?", (status, error, post_id, tid))
    c.commit()


def advance(tid, inp):
    cfg = {"configurable": {"thread_id": tid}}
    try:
        agent.invoke(inp, cfg)
        st = agent.get_state(cfg)
        if any(t.interrupts for t in st.tasks):
            set_run(tid, "waiting")
        elif st.values.get("post_id"):
            set_run(tid, "done", post_id=st.values["post_id"])
        else:
            set_run(tid, "discarded")
    except Exception as e:  # noqa: BLE001
        set_run(tid, "error", error=str(e)[:300])


def img_url(path):
    return "/images/" + os.path.basename(path)


@app.before_request
def guard():
    if request.host.split(":")[0] not in ALLOWED_HOSTS:
        abort(403)
    if request.endpoint == "asset" and request.view_args.get("name") in PUBLIC_ASSETS:
        return
    if PASSWORD and not session.get("ok") and request.endpoint != "login":
        if request.path.startswith("/api/"):
            return jsonify(error="Please log in"), 401
        return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not PASSWORD:
        return redirect("/")
    err = ""
    if request.method == "POST":
        now = time.time()
        FAILS[:] = [t for t in FAILS if now - t < 600]
        if len(FAILS) >= 10:
            err = "Too many attempts. Try again in a few minutes."
        elif hmac.compare_digest(request.form.get("password", "").encode(), PASSWORD.encode()):
            session.clear()
            session["ok"] = True
            session.permanent = True
            return redirect("/")
        else:
            FAILS.append(now)
            err = "Wrong password."
    return Response(LOGIN_HTML.replace("{err}", err), mimetype="text/html")


@app.get("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.get("/")
def index():
    return send_from_directory(os.path.join(BASE, "static"), "index.html")


@app.get("/images/<name>")
def image(name):
    return send_from_directory(IMG, name)


@app.get("/assets/<name>")
def asset(name):
    return send_from_directory(os.path.join(BASE, "static"), name, max_age=86400)


@app.get("/api/state")
def state():
    runs = []
    for r in db.conn().execute(
            "SELECT * FROM runs WHERE status IN ('working','waiting','error') ORDER BY created_at DESC"):
        item = dict(r)
        if r["status"] == "waiting":
            cfg = {"configurable": {"thread_id": r["thread_id"]}}
            for t in agent.get_state(cfg).tasks:
                if t.interrupts:
                    v = t.interrupts[0].value
                    paths = v.get("image_paths") or [v["image_path"]]
                    item["review"] = {"caption": v["caption"], "issues": v["issues"],
                                      "image_prompts": v.get("image_prompts") or [v.get("image_prompt", "")],
                                      "image_urls": [img_url(p) for p in paths], "image_url": img_url(paths[0]),
                                      "uploaded": bool(v.get("uploaded")),
                                      "slide_texts": v.get("slide_texts") or []}
        item["timeline"] = db.timeline(r["thread_id"])
        runs.append(item)
    posts = []
    for p in db.all_posts():
        d = dict(p)
        urls = [img_url(x) for x in db.unpack(d["image_path"])]
        d["image_urls"], d["image_url"] = urls, urls[0]
        d["timeline"] = db.timeline(d["thread_id"]) if d.get("thread_id") else None
        posts.append(d)
    return jsonify(runs=runs, posts=posts)


@app.post("/api/upload")
def upload():
    if request.headers.get("X-Requested-With") != "fetch":
        return jsonify(error="Bad request"), 400
    files = request.files.getlist("files")
    if not files:
        return jsonify(error="Choose at least one image"), 400
    if len(files) > G.MAX_SLIDES:
        return jsonify(error=f"Up to {G.MAX_SLIDES} images per post"), 400
    saved = []
    for f in files:
        try:
            name = images.save_upload(f.stream)
        except ValueError as e:
            return jsonify(error=f"{f.filename or 'A file'} {e}"), 400
        except Exception as e:  # noqa: BLE001
            return jsonify(error=f"Upload failed ({type(e).__name__}: {e})"), 500
        saved.append({"name": name, "url": "/images/" + name})
    return jsonify(files=saved)


@app.post("/api/new")
def new():
    body = request.get_json() or {}
    topic = (body.get("topic") or "").strip()
    names = body.get("uploads") or []
    state = {}
    if names:
        if not isinstance(names, list) or len(names) > G.MAX_SLIDES or not all(isinstance(n, str) for n in names):
            return jsonify(error=f"Up to {G.MAX_SLIDES} images per post"), 400
        paths = []
        for n in names:
            if not re.fullmatch(r"[0-9a-f]{8}\.jpg", n) or not os.path.isfile(os.path.join(IMG, n)):
                return jsonify(error="One of the uploaded images is missing. Upload it again."), 400
            paths.append(os.path.join(images.IMG_DIR, n))
        state = {"uploaded": True, "image_paths": paths, "hint": topic}
        topic = topic or f"Caption for {len(paths)} uploaded image{'s' if len(paths) > 1 else ''}"
    else:
        if not topic:
            return jsonify(error="Enter a topic first"), 400
        try:
            state["slides"] = max(1, min(G.MAX_SLIDES, int(body.get("slides") or 1)))
        except (TypeError, ValueError):
            state["slides"] = 1
        state["overlay"] = bool(body.get("overlay"))
    tid = uuid.uuid4().hex[:8]
    c = db.conn()
    c.execute("INSERT INTO runs (thread_id, topic, status, created_at) VALUES (?,?,'working',?)",
              (tid, topic, db.now()))
    c.commit()
    state.update(topic=topic, thread_id=tid)
    threading.Thread(target=advance, args=(tid, state), daemon=True).start()
    return jsonify(thread_id=tid)


@app.post("/api/decide/<tid>")
def decide(tid):
    d = request.get_json() or {}
    action = d.get("action")
    if action not in ("approve", "edit", "reject", "new_image", "remove_slide", "edit_text"):
        return jsonify(error="Bad action"), 400
    c = db.conn()
    cur = c.execute("UPDATE runs SET status='working' WHERE thread_id=? AND status='waiting'", (tid,))
    c.commit()
    if cur.rowcount != 1:
        return jsonify(error="This post is not waiting for approval"), 409
    resume = {"action": action}
    if action in ("approve", "edit"):
        try:
            mins = max(0, int(d.get("minutes", 0)))
        except (TypeError, ValueError):
            mins = 0
        when = datetime.now(timezone.utc) + timedelta(minutes=mins)
        resume.update(publish_at=when.isoformat(timespec="seconds"), approved_by="web-user")
        if action == "edit":
            resume["caption"] = (d.get("caption") or "").strip()
    elif action in ("new_image", "remove_slide", "edit_text"):
        try:
            resume["slide"] = int(d.get("slide", 0))
        except (TypeError, ValueError):
            resume["slide"] = 0
        if action == "new_image":
            resume["image_prompt"] = (d.get("image_prompt") or "").strip()
        if action == "edit_text":
            resume["title"] = (d.get("title") or "").strip()
            resume["body"] = (d.get("body") or "").strip()
    else:
        resume["feedback"] = (d.get("feedback") or "").strip()
    threading.Thread(target=advance, args=(tid, Command(resume=resume)), daemon=True).start()
    return jsonify(ok=True)


@app.post("/api/dismiss/<tid>")
def dismiss(tid):
    request.get_json()
    set_run(tid, "discarded")
    return jsonify(ok=True)


@app.post("/api/retry/<int:pid>")
def retry(pid):
    request.get_json()
    c = db.conn()
    c.execute("UPDATE posts SET status='scheduled', error=NULL WHERE id=? AND status='failed'", (pid,))
    c.commit()
    return jsonify(ok=True)


@app.post("/api/reschedule/<int:pid>")
def reschedule(pid):
    raw = (request.get_json() or {}).get("publish_at", "")
    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        when = when.astimezone(timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return jsonify(error="That date is not valid"), 400
    c = db.conn()
    cur = c.execute("UPDATE posts SET publish_at=? WHERE id=? AND status='scheduled'", (when, pid))
    c.commit()
    if cur.rowcount != 1:
        return jsonify(error="Only scheduled posts can be moved"), 409
    return jsonify(ok=True)


@app.post("/api/cancel/<int:pid>")
def cancel(pid):
    request.get_json()
    c = db.conn()
    cur = c.execute("DELETE FROM posts WHERE id=? AND status='scheduled'", (pid,))
    c.commit()
    if cur.rowcount != 1:
        return jsonify(error="Only scheduled posts can be cancelled"), 409
    return jsonify(ok=True)


@app.get("/api/style")
def style_get():
    total, kept = db.style_stats()
    rows = db.style_examples(5)
    return jsonify(enabled=os.getenv("LEARN_STYLE", "1") == "1", total=total, kept=kept,
                   examples=[{"final": r["final"], "edited": bool(r["edited"])} for r in rows])


@app.post("/api/style/reset")
def style_reset():
    request.get_json()
    db.style_reset()
    return jsonify(ok=True)


def worker_loop():
    while True:
        try:
            for p in db.due_posts():
                if not db.claim(p["id"]):
                    continue
                print(f"Publishing #{p['id']} to {p['platform']}")
                t0 = time.perf_counter()
                try:
                    publisher.PUBLISHERS[p["platform"]](p["caption"], p["image_path"])
                    db.finish(p["id"], True)
                    db.log_event(p["thread_id"], "publish", "done", f"Post #{p['id']} live on {p['platform']}",
                                 round((time.perf_counter() - t0) * 1000))
                except Exception as e:  # noqa: BLE001
                    db.finish(p["id"], False, str(e))
                    db.log_event(p["thread_id"], "publish", "error", str(e)[:200],
                                 round((time.perf_counter() - t0) * 1000))
                    print(f"  failed: {e}")
        except Exception as e:  # noqa: BLE001
            print("worker error:", e)
        time.sleep(15)


if __name__ == "__main__":
    if not PASSWORD and any(h not in ("127.0.0.1", "localhost") for h in ALLOWED_HOSTS):
        raise SystemExit("Set APP_PASSWORD in .env before allowing a public host in ALLOWED_HOSTS.")
    init()
    if os.getenv("WORKER", "1") == "1":
        threading.Thread(target=worker_loop, daemon=True).start()
    port = int(os.getenv("PORT", "5000"))
    print(f"Open http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)