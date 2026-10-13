"""Text generation. TEXT_PROVIDER=gemini (default, free tier) or claude. Can also look at images."""
import base64
import io
import json
import os
import time
import urllib.error
import urllib.request

from PIL import Image

import net


def _thumb_b64(path: str) -> str:
    img = Image.open(path).convert("RGB")
    img.thumbnail((768, 768))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


def ask(prompt: str, json_mode: bool = False, images=None) -> str:
    if os.getenv("TEXT_PROVIDER", "gemini").lower() == "claude":
        return _claude(prompt, images or [])
    return _gemini(prompt, json_mode, images or [])


def _gemini(prompt: str, json_mode: bool, images) -> str:
    key = os.environ["GEMINI_API_KEY"]
    models = [m.strip() for m in os.getenv(
        "GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-2.5-flash-lite,gemini-3.5-flash"
    ).split(",") if m.strip()]
    parts = [{"text": prompt}] + [
        {"inline_data": {"mime_type": "image/jpeg", "data": _thumb_b64(p)}} for p in images]
    body = {"contents": [{"parts": parts}]}
    if json_mode:
        body["generationConfig"] = {"responseMimeType": "application/json"}

    last = None
    for rnd in range(3):  # if every model is busy, wait and go through the list again
        busy = False
        for model in models:  # a busy or retired model just moves us to the next one
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(),
                headers={**net.UA, "x-goog-api-key": key, "Content-Type": "application/json"},
            )
            try:
                data = json.loads(net.http(req, timeout=120, label=model))
                out = data["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in out).strip()
                if text:
                    return text
                last = f"{model}: empty reply"
            except urllib.error.HTTPError as e:
                last = f"{model}: HTTP {e.code} {e.read().decode(errors='replace')[:200]}"
                busy = busy or e.code in net.RETRY_CODES
            except (urllib.error.URLError, TimeoutError) as e:
                last = f"{model}: network error {e}"
                busy = True
            except (KeyError, IndexError):
                last = f"{model}: no usable candidate (blocked or malformed reply)"
            print(f"  {last} -> trying next model")
        if not busy or rnd == 2:
            break
        wait = 15 * (rnd + 1)
        print(f"  all models busy, waiting {wait}s before trying again")
        time.sleep(wait)
    raise RuntimeError(f"All Gemini models failed. Last problem: {last}")


def _claude(prompt: str, images) -> str:
    from langchain_anthropic import ChatAnthropic

    model = ChatAnthropic(model=os.getenv("MODEL", "claude-haiku-4-5-20251001"), max_tokens=1000)
    if not images:
        return model.invoke(prompt).content.strip()
    from langchain_core.messages import HumanMessage

    parts = [{"type": "text", "text": prompt}] + [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + _thumb_b64(p)}} for p in images]
    return model.invoke([HumanMessage(content=parts)]).content.strip()
