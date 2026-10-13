"""CLI.
  python main.py new "topic"     draft + image -> approve in terminal -> schedule
  python main.py resume <id>     continue a run that was paused (e.g. after a restart)
  python main.py worker          publish due posts (run in a second terminal)
  python main.py list            show recent posts
"""
import argparse
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

load_dotenv()
import db  # noqa: E402
import graph as G  # noqa: E402
import publisher  # noqa: E402


def get_app():
    cp = SqliteSaver(sqlite3.connect("instagram_checkpoints.db", check_same_thread=False))
    return G.build(cp)


def pending_interrupt(app, cfg):
    for t in app.get_state(cfg).tasks:
        if t.interrupts:
            return t.interrupts[0].value
    return None


def human_review(p):
    print("\n--- PENDING APPROVAL ---")
    print(p["caption"])
    print(f"({len(p['caption'])}/{G.MAX_CAPTION} chars)")
    print("Images: " + ", ".join(p["image_paths"]))
    print("Image prompt: " + p["image_prompts"][0])
    for issue in p["issues"]:
        print(f"  WARNING: {issue}")
    if hasattr(os, "startfile"):  # Windows: open the image so you can actually look at it
        os.startfile(os.path.abspath(p["image_paths"][0]))
    while True:
        c = input("[a]pprove  [e]dit caption  [i]mage redo  [r]eject  > ").strip().lower()
        if c in ("a", "e", "i", "r"):
            break
    if c == "r":
        return {"action": "reject",
                "feedback": input("Feedback for a redraft (blank = discard): ").strip()}
    if c == "i":
        return {"action": "new_image",
                "image_prompt": input("New image prompt (blank = same prompt, new image): ").strip()}
    caption = input("New caption: ").strip() if c == "e" else None
    mins = input("Publish in how many minutes? [0] ").strip() or "0"
    when = datetime.now(timezone.utc) + timedelta(minutes=int(mins))
    return {"action": "approve" if c == "a" else "edit", "caption": caption,
            "publish_at": when.isoformat(timespec="seconds"), "approved_by": "cli-user"}


def drive(app, cfg, inp):
    app.invoke(inp, cfg)
    while (payload := pending_interrupt(app, cfg)) is not None:
        app.invoke(Command(resume=human_review(payload)), cfg)
    state = app.get_state(cfg).values
    if state.get("post_id"):
        print(f"\nScheduled post #{state['post_id']}. Run `python main.py worker` to publish.")
    else:
        print("\nDiscarded. Nothing scheduled.")


def cmd_new(a):
    tid = uuid.uuid4().hex[:8]
    print(f"thread {tid}")
    drive(get_app(), {"configurable": {"thread_id": tid}}, {"topic": a.topic, "thread_id": tid})


def cmd_resume(a):
    drive(get_app(), {"configurable": {"thread_id": a.thread_id}}, None)


def cmd_worker(a):
    print("Worker running. Ctrl+C to stop.")
    while True:
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
        if a.once:
            break
        time.sleep(15)


def cmd_list(_):
    for p in db.all_posts():
        print(f"#{p['id']} [{p['status']}] {p['publish_at']} {p['caption'][:60]!r}")
        if p["error"]:
            print(f"    error: {p['error']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("new"); s.add_argument("topic"); s.set_defaults(fn=cmd_new)
    s = sub.add_parser("resume"); s.add_argument("thread_id"); s.set_defaults(fn=cmd_resume)
    s = sub.add_parser("worker"); s.add_argument("--once", action="store_true"); s.set_defaults(fn=cmd_worker)
    s = sub.add_parser("list"); s.set_defaults(fn=cmd_list)
    a = ap.parse_args()
    a.fn(a)