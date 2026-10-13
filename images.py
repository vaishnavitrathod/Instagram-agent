"""Image generation + public hosting. Instagram needs JPEG at a public URL.

IMAGE_PROVIDERS is tried in order, e.g. "pollinations,cloudflare,openai".
"""
import base64
import io
import json
import os
import random
import re
import textwrap
import urllib.request
import uuid
from urllib.parse import quote

from PIL import Image, ImageDraw, ImageFont, ImageOps

import net

IMG_DIR = "images"


def _placeholder(prompt):
    img = Image.new("RGB", (1024, 1024), (40, 44, 80))
    ImageDraw.Draw(img).multiline_text(
        (60, 60), "PLACEHOLDER IMAGE\n\n" + textwrap.fill(prompt, 40), fill="white")
    return img


def _pollinations(prompt):
    key = os.getenv("POLLINATIONS_API_KEY")
    model = os.getenv("POLLINATIONS_MODEL", "flux")
    url = (f"https://gen.pollinations.ai/image/{quote(prompt[:900])}"
           f"?model={model}&width=1024&height=1024&seed={random.randint(1, 10**6)}")
    headers = dict(net.UA)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = net.http(urllib.request.Request(url, headers=headers), timeout=180, label="pollinations")
    return Image.open(io.BytesIO(data))


def _cloudflare(prompt):  # untested: needs CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN
    acct, token = os.environ["CLOUDFLARE_ACCOUNT_ID"], os.environ["CLOUDFLARE_API_TOKEN"]
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/@cf/black-forest-labs/flux-1-schnell"
    req = urllib.request.Request(
        url, data=json.dumps({"prompt": prompt[:2000], "steps": 4}).encode(),
        headers={**net.UA, "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    data = json.loads(net.http(req, timeout=120, label="cloudflare"))
    return Image.open(io.BytesIO(base64.b64decode(data["result"]["image"])))


def _openai(prompt):
    from openai import OpenAI

    res = OpenAI().images.generate(
        model=os.getenv("IMAGE_MODEL", "gpt-image-1"), prompt=prompt, size="1024x1024")
    return Image.open(io.BytesIO(base64.b64decode(res.data[0].b64_json)))


PROVIDERS = {"pollinations": _pollinations, "cloudflare": _cloudflare, "openai": _openai}


def generate_image(prompt: str) -> str:
    """Generate an image, save as JPEG, return the local path."""
    os.makedirs(IMG_DIR, exist_ok=True)
    path = os.path.join(IMG_DIR, f"{uuid.uuid4().hex[:8]}.jpg")

    if os.getenv("FAKE_LLM") == "1":
        img = _placeholder(prompt)
    else:
        img, errors = None, []
        names = [n.strip() for n in os.getenv("IMAGE_PROVIDERS", "pollinations").split(",") if n.strip()]
        for name in names:
            try:
                img = PROVIDERS[name](prompt)
                img.load()  # make sure it is a real, decodable image
                break
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {e}")
                print(f"  image provider {name} failed: {e}")
        if img is None:  # never fall back to a placeholder when really posting
            raise RuntimeError("All image providers failed: " + " | ".join(errors))

    img.convert("RGB").save(path, "JPEG", quality=92)  # Instagram: JPEG only
    return path


def save_upload(stream) -> str:
    """Validate an uploaded image, make it Instagram-safe, save as JPEG, return the file name.
    Instagram only accepts JPEG, aspect ratios from 4:5 to 1.91:1, and at least 320 px."""
    try:
        img = Image.open(stream)
        img.load()
    except Exception:  # noqa: BLE001
        raise ValueError("could not be opened. Use a JPEG, PNG or WebP file (iPhone HEIC photos must be converted first)")
    if img.width * img.height > 40_000_000:
        raise ValueError("image is too large")
    img = ImageOps.exif_transpose(img)  # respect phone rotation
    if img.mode in ("RGBA", "LA", "P"):  # flatten transparency onto white
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, "white")
        flat.paste(img, mask=img.split()[-1])
        img = flat
    img = img.convert("RGB")
    w, h = img.size
    if min(w, h) < 80:
        raise ValueError(f"is too small to use ({w}x{h} pixels)")
    if min(w, h) < 320:  # Instagram needs at least 320 px, so scale small images up
        scale = min(640 / min(w, h), 8)
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
        w, h = img.size
    if w / h < 0.81:  # taller than 4:5 -> crop top and bottom
        nh = int(w / 0.81)
        top = (h - nh) // 2
        img = img.crop((0, top, w, top + nh))
    elif w / h > 1.9:  # wider than 1.91:1 -> crop the sides
        nw = int(h * 1.9)
        left = (w - nw) // 2
        img = img.crop((left, 0, left + nw, h))
    img.thumbnail((1440, 1440))
    os.makedirs(IMG_DIR, exist_ok=True)
    name = uuid.uuid4().hex[:8] + ".jpg"
    img.save(os.path.join(IMG_DIR, name), "JPEG", quality=92)
    return name


def _font(size: int, bold: bool = False):
    names = ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"]
    for n in ([os.getenv("TEXT_FONT")] if os.getenv("TEXT_FONT") else []) + names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    lines = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            test = (line + " " + word).strip()
            if draw.textlength(test, font=font) <= max_w:
                line = test
            else:
                if line:
                    lines.append(line)
                line = word
        lines.append(line)
    return lines


def _clean(t) -> str:
    # fonts cannot draw colour emoji, so keep letters, digits and common punctuation only
    return re.sub(r"[^\w\s.,!?'’\-:;()/&%+=#@\"$*]", "", str(t or "")).strip()


def render_slide(raw_path: str, text: dict, idx: int, total: int) -> str:
    """Print a headline and short text on a card over the image. Returns the new file path."""
    title, body = _clean(text.get("title")), _clean(text.get("body"))
    img = Image.open(raw_path).convert("RGB")
    W, H = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    pad = int(W * 0.05)
    inner = W - 4 * pad
    for scale in (1.0, 0.85, 0.72, 0.6):  # shrink the type until it fits
        ts, bs = int(W * 0.064 * scale), int(W * 0.042 * scale)
        tf, bf = _font(ts, True), _font(bs)
        tl = _wrap(d, title, tf, inner) if title else []
        bl = _wrap(d, body, bf, inner) if body else []
        th, bh = int(len(tl) * ts * 1.22), int(len(bl) * bs * 1.35)
        gap = int(ts * 0.4) if tl and bl else 0
        card_h = pad + th + gap + bh + pad
        if card_h <= H * 0.58:
            break
    y0 = H - pad - card_h
    d.rounded_rectangle((pad, y0, W - pad, H - pad), radius=int(W * 0.04), fill=(255, 255, 255, 232))
    y = y0 + pad
    for ln in tl:
        d.text((2 * pad, y), ln, font=tf, fill=(42, 33, 64, 255))
        y += int(ts * 1.22)
    y += gap
    for ln in bl:
        d.text((2 * pad, y), ln, font=bf, fill=(95, 84, 125, 255))
        y += int(bs * 1.35)
    if total > 1:  # slide counter
        cf, label = _font(int(W * 0.036), True), f"{idx}/{total}"
        tw = d.textlength(label, font=cf)
        px, py = int(W * 0.03), int(W * 0.016)
        x1, y1 = W - pad, pad
        d.rounded_rectangle((x1 - tw - 2 * px, y1, x1, y1 + int(W * 0.036) + 2 * py), radius=99, fill=(111, 94, 232, 235))
        d.text((x1 - tw - px, y1 + py), label, font=cf, fill=(255, 255, 255, 255))
    out = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    os.makedirs(IMG_DIR, exist_ok=True)
    path = os.path.join(IMG_DIR, uuid.uuid4().hex[:8] + ".jpg")
    out.save(path, "JPEG", quality=92)
    return path


def upload_public(path: str) -> str:
    """Upload to Cloudinary and return a public https URL (reads CLOUDINARY_URL)."""
    import cloudinary.uploader

    return cloudinary.uploader.upload(path, folder="ig-agent")["secure_url"]