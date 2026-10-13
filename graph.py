"""LangGraph workflow:
draft (caption + image prompts) -> image(s) -> review -> [human approval pause] -> schedule
Supports single-image posts and carousels of 2-10 slides."""
import functools
import json
import os
import re
import time
from typing import TypedDict

from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

import db
import images
import llm

PLATFORM = "instagram"
MAX_CAPTION = 2200
MAX_HASHTAGS = 30
MAX_REVISIONS = 3
MAX_SLIDES = 10
BANNED = [w.strip().lower() for w in os.getenv("BANNED_WORDS", "").split(",") if w.strip()]


class State(TypedDict, total=False):
    topic: str
    thread_id: str
    slides: int
    uploaded: bool
    hint: str
    overlay: bool
    slide_texts: list
    raw_paths: list
    caption: str
    image_prompts: list
    image_paths: list
    redo: list
    issues: list
    feedback: str
    revisions: int
    decision: dict
    post_id: int


def _style_block() -> str:
    """Recent approved captions, so new drafts match the owner's voice."""
    if os.getenv("LEARN_STYLE", "1") != "1":
        return ""
    ex = db.style_examples(6)
    if not ex:
        return ""
    lines = ["\nStyle guide learned from this account's approved posts. Match their voice, length, emoji use "
             "and hashtag habits. Do not copy them or reuse their facts:"]
    lines += [f"{i}. {r['final'][:500]}" for i, r in enumerate(ex, 1)]
    changed = [r for r in ex if r["edited"]][:2]
    if changed:
        lines.append("\nThe owner edited some AI drafts. What they changed (before -> after):")
        lines += [f"- {r['draft'][:300]} -> {r['final'][:300]}" for r in changed]
    lines.append("\nReply with ONLY the JSON object described above.")
    return "\n".join(lines)


def _caption_from_images(state: State) -> str:
    paths = state["image_paths"]
    many = len(paths) > 1
    prompt = (
        "Write an Instagram caption for a post made from the attached image"
        + ("s (a carousel, shown in slide order)" if many else "") + ".\n"
        f"Brand voice: {os.getenv('BRAND_VOICE', 'friendly')}\n"
    )
    if state.get("hint"):
        prompt += f"What the post is about, from the account owner: {state['hint']}\n"
    prompt += ("Describe only what you can actually see or were told. Do not invent names, places, dates or facts.\n"
               'Reply with ONLY a JSON object, no code fences: {"caption": "..."} '
               "(hook in the first line, under 2000 chars, 3-8 relevant hashtags at the end)\n")
    if state.get("feedback"):
        prompt += f"\nPrevious caption: {state['caption']}\nReviewer feedback to address: {state['feedback']}\n"
    raw = re.sub(r"^```(?:json)?|```$", "", llm.ask(prompt + _style_block(), json_mode=True, images=paths).strip(), flags=re.M).strip()
    return json.loads(raw)["caption"]


def draft_node(state: State):
    n = state.get("revisions", 0) + 1
    k = max(1, min(MAX_SLIDES, int(state.get("slides") or 1)))
    if state.get("uploaded"):  # the person supplied the images, so only write the caption
        paths = state["image_paths"]
        cap = (f"Draft #{n}: a caption for your {len(paths)} photo(s) #demo" if os.getenv("FAKE_LLM") == "1"
               else _caption_from_images(state))
        return {"caption": cap, "image_prompts": [""] * len(paths), "revisions": n, "feedback": "", "redo": []}
    if os.getenv("FAKE_LLM") == "1":
        return {"caption": f"Draft #{n}: a post about {state['topic']} #demo",
                "image_prompts": [f"A bright illustration about {state['topic']}, part {i + 1}" for i in range(k)],
                "slide_texts": ([{"title": f"Tip {i + 1}", "body": "Example text for this slide"} for i in range(k)]
                                if state.get("overlay") else []),
                "revisions": n, "feedback": "", "redo": []}
    if k == 1:
        shape = ('  "image_prompts": an array with ONE detailed prompt for an image generator describing a '
                 "visually striking, square image that matches the caption (no text or logos in the image)\n")
        cap_rule = "under 2000 chars"
    else:
        shape = (f'  "image_prompts": an array of exactly {k} detailed image-generator prompts, one per slide, '
                 "all in one consistent visual style, each slide showing a different step or idea of the post "
                 "(square images, no text or logos in the images)\n")
        cap_rule = "under 2000 chars, and tell people to swipe"
    prompt = (
        f"Create an Instagram {'carousel ' if k > 1 else ''}post about: {state['topic']}\n"
        f"Brand voice: {os.getenv('BRAND_VOICE', 'friendly')}\n"
        "Reply with ONLY a JSON object, no code fences, with these keys:\n"
        f'  "caption": the caption (hook in first line, {cap_rule}, 3-8 relevant hashtags at the end)\n'
        + shape
    )
    if state.get("overlay"):
        prompt += (
            f'  "slide_texts": an array of exactly {k} objects, one per slide, each {{"title": up to 6 words, '
            '"body": up to 18 words}. These words are printed on the images, so write them as the final slide copy: '
            "one distinct idea or tip per slide, plain text, no emoji. For a single-image post put the quote or message in body.\n"
            "The image_prompts must describe artwork with NO text, letters, numbers or logos, because the text is added separately.\n")
    if state.get("feedback"):
        prompt += f"\nPrevious caption: {state['caption']}\nReviewer feedback to address: {state['feedback']}\n"
    raw = re.sub(r"^```(?:json)?|```$", "", llm.ask(prompt + _style_block(), json_mode=True).strip(), flags=re.M).strip()
    data = json.loads(raw)
    prompts = data.get("image_prompts") or [data.get("image_prompt", "")]
    prompts = [p for p in prompts if isinstance(p, str) and p.strip()][:k]
    while len(prompts) < k:
        prompts.append(prompts[-1] if prompts else state["topic"])
    texts = []
    if state.get("overlay"):
        for t in (data.get("slide_texts") or [])[:k]:
            if isinstance(t, dict):
                texts.append({"title": str(t.get("title") or "")[:80], "body": str(t.get("body") or "")[:240]})
            else:
                texts.append({"title": "", "body": str(t)[:240]})
        while len(texts) < k:
            texts.append({"title": "", "body": ""})
    return {"caption": data["caption"], "image_prompts": prompts, "slide_texts": texts,
            "revisions": n, "feedback": "", "redo": []}


def _render(raw: str, texts, i: int, n: int) -> str:
    """Print the slide text on the image if there is any; otherwise use the plain image."""
    t = texts[i] if texts and i < len(texts) else None
    if t and (t.get("title") or t.get("body")):
        return images.render_slide(raw, t, i + 1, n)
    return raw


def image_node(state: State):
    if state.get("uploaded"):
        return {"redo": []}
    prompts = state["image_prompts"]
    n = len(prompts)
    texts = state.get("slide_texts") or []
    redo = state.get("redo") or []
    raws, finals = list(state.get("raw_paths") or []), list(state.get("image_paths") or [])
    if redo and len(raws) == n and len(finals) == n:
        idxs = redo
    else:
        raws, finals, idxs = [None] * n, [None] * n, range(n)
    for i in idxs:
        raws[i] = images.generate_image(prompts[i])
        finals[i] = _render(raws[i], texts, i, n)
    return {"raw_paths": raws, "image_paths": finals, "redo": []}


def review_node(state: State):
    text = state["caption"].strip()
    if len(text) > MAX_CAPTION:
        text = text[: MAX_CAPTION - 1] + "…"
    issues = [f"contains banned term '{w}'" for w in BANNED if w in text.lower()]
    tags = re.findall(r"#\w+", text)
    if len(tags) > MAX_HASHTAGS:
        issues.append(f"{len(tags)} hashtags (Instagram allows {MAX_HASHTAGS})")
    return {"caption": text, "issues": issues}


def approval_node(state: State):
    # Graph pauses here; state is saved by the checkpointer until a human resumes it.
    try:
        decision = interrupt({
            "caption": state["caption"],
            "image_paths": state["image_paths"],
            "image_prompts": state["image_prompts"],
            "issues": state.get("issues", []),
            "uploaded": bool(state.get("uploaded")),
            "slide_texts": state.get("slide_texts") or [],
        })
    except GraphInterrupt:
        db.log_waiting(state.get("thread_id"))
        raise
    db.log_decision(state.get("thread_id"), decision.get("action", ""))
    return {"decision": decision}


def after_approval(state: State):
    d = state["decision"]
    if d["action"] in ("approve", "edit"):
        return "schedule"
    if d["action"] == "new_image":
        return "review" if state.get("uploaded") else "reimage"
    if d["action"] == "remove_slide":
        return "removeslide"
    if d["action"] == "edit_text":
        return "retext"
    if d.get("feedback") and state.get("revisions", 0) < MAX_REVISIONS:
        return "redraft"
    return END


def redraft_node(state: State):
    return {"feedback": state["decision"]["feedback"]}


def _slide_index(state: State):
    try:
        i = int(state["decision"].get("slide", 0))
    except (TypeError, ValueError):
        i = 0
    return min(max(i, 0), len(state["image_prompts"]) - 1)


def reimage_node(state: State):
    prompts = list(state["image_prompts"])
    i = _slide_index(state)
    if state["decision"].get("image_prompt"):
        prompts[i] = state["decision"]["image_prompt"]
    return {"image_prompts": prompts, "redo": [i]}


def removeslide_node(state: State):
    prompts, paths = list(state["image_prompts"]), list(state["image_paths"])
    raws, texts = list(state.get("raw_paths") or []), list(state.get("slide_texts") or [])
    if len(prompts) > 1:
        i = _slide_index(state)
        del prompts[i]
        del paths[i]
        if len(raws) == len(paths) + 1 and len(texts) == len(raws):
            del raws[i]
            del texts[i]
            paths = [_render(raws[j], texts, j, len(raws)) for j in range(len(raws))]  # renumber the counters
    return {"image_prompts": prompts, "image_paths": paths, "raw_paths": raws, "slide_texts": texts}


def retext_node(state: State):
    d = state["decision"]
    i = _slide_index(state)
    texts = [dict(t) for t in (state.get("slide_texts") or [])]
    raws, finals = state.get("raw_paths") or [], list(state["image_paths"])
    if len(texts) == len(finals) == len(raws):
        texts[i] = {"title": (d.get("title") or "").strip()[:80], "body": (d.get("body") or "").strip()[:240]}
        finals[i] = _render(raws[i], texts, i, len(finals))
    return {"slide_texts": texts, "image_paths": finals}


def schedule_node(state: State):
    d = state["decision"]
    caption = d.get("caption") if d["action"] == "edit" and d.get("caption") else state["caption"]
    if os.getenv("FAKE_LLM") != "1":
        try:
            db.add_style(state["caption"], caption)
        except Exception as e:  # noqa: BLE001  (learning must never block scheduling)
            print("style memory skipped:", e)
    post_id = db.add_scheduled(PLATFORM, caption, db.pack(state["image_paths"]),
                               d.get("approved_by", "human"), d["publish_at"], state["thread_id"])
    return {"post_id": post_id}


def traced(step, detail=None):
    """Wrap a node so each run is logged to the activity timeline with its duration."""
    def deco(fn):
        @functools.wraps(fn)
        def run(state):
            t0, tid = time.perf_counter(), state.get("thread_id")
            try:
                out = fn(state)
            except Exception as e:  # noqa: BLE001
                db.log_event(tid, step, "error", str(e)[:200], round((time.perf_counter() - t0) * 1000))
                raise
            ms = round((time.perf_counter() - t0) * 1000)
            note = ""
            if detail:
                try:
                    note = detail(state, out)
                except Exception:  # noqa: BLE001
                    pass
            db.log_event(tid, step, "done", note, ms)
            return out
        return run
    return deco


def _image_note(s, o):
    if s.get("uploaded"):
        return "Using your photos"
    if s.get("redo"):
        return f"Redid slide {s['redo'][0] + 1}"
    return f"{len(o['image_paths'])} image(s)"


def build(checkpointer):
    g = StateGraph(State)
    g.add_node("draft", traced("draft", lambda s, o: f"Revision {o['revisions']}, {len(o['caption'])} chars")(draft_node))
    g.add_node("image", traced("image", _image_note)(image_node))
    g.add_node("review", traced("review", lambda s, o: "; ".join(o["issues"]) or "No problems found")(review_node))
    g.add_node("approval", approval_node)
    g.add_node("redraft", traced("redraft", lambda s, o: o["feedback"][:80])(redraft_node))
    g.add_node("reimage", traced("reimage")(reimage_node))
    g.add_node("removeslide", traced("removeslide")(removeslide_node))
    g.add_node("retext", traced("retext")(retext_node))
    g.add_node("schedule", traced("schedule", lambda s, o: f"Post #{o['post_id']}")(schedule_node))
    g.add_edge(START, "draft")
    g.add_edge("draft", "image")
    g.add_edge("image", "review")
    g.add_edge("review", "approval")
    g.add_conditional_edges("approval", after_approval, ["schedule", "redraft", "reimage", "removeslide", "retext", "review", END])
    g.add_edge("redraft", "draft")
    g.add_edge("reimage", "image")
    g.add_edge("removeslide", "review")
    g.add_edge("retext", "review")
    g.add_edge("schedule", END)
    return g.compile(checkpointer=checkpointer)