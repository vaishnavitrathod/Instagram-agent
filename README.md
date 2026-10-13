# AI Instagram Agent (MVP) - free-tier edition

caption + image prompt (Gemini) -> image (Pollinations) -> review -> **human approval (graph pauses)** -> schedule -> publish to Instagram

## Setup (Windows PowerShell)
    python -m venv .venv
    .venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    copy .env.example .env      # then fill in the keys

## Try it with no keys at all
    $env:FAKE_LLM="1"; $env:DRY_RUN="1"
    python main.py new "my topic"
    python main.py worker --once

## Real run
    python main.py new "launching our hackathon project"
    python main.py worker       # second terminal; publishes due posts
    python main.py list

At the approval prompt: [a]pprove, [e]dit caption, [i]mage redo, [r]eject (with feedback).
On Windows the image opens automatically for review. Keep DRY_RUN=1 until the flow looks right.

## Free services used
- Captions: Google Gemini (GEMINI_API_KEY). A busy or retired model is retried, then the next
  model in GEMINI_MODELS is tried. Edit that list in .env when Google retires a name.
- Images: Pollinations (POLLINATIONS_API_KEY). Add backups in IMAGE_PROVIDERS (cloudflare, openai).
  If every image provider fails the run stops with an error; it never posts a placeholder.
- Hosting: Cloudinary free plan. Posting: Instagram Graph API (no charge).

## Switching back to paid providers
TEXT_PROVIDER=claude (pip install langchain-anthropic, set ANTHROPIC_API_KEY, MODEL)
IMAGE_PROVIDERS=openai (pip install openai, set OPENAI_API_KEY)
