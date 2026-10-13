# Instagram Agent

An AI agent that drafts, designs and schedules Instagram posts, with a **human approval step before anything is published**.

Describe a post (or upload your own photos), and the agent writes the caption, creates the images, and then **pauses** until you review it. Approved posts go onto a content calendar and are published automatically through the Instagram Graph API. The whole stack runs on free tiers.

<!-- Add a screenshot or a short demo GIF of the dashboard here -->
<img width="1842" height="908" alt="Screenshot 2026-10-14 044244" src="https://github.com/user-attachments/assets/d01fe693-59a8-4560-a03c-0de946e7e696" />



## Features

**Creating posts**
- **AI image posts** and **AI carousels** (2 to 10 slides), written and illustrated from one prompt
- **Text on slides**: the agent writes a headline and tip for each slide and prints it onto the artwork (image generators cannot spell reliably, so the text is added separately). Every slide's text is editable
- **Your own images**: upload up to 10 photos and the agent writes the caption by looking at them
- **Learns your style**: your approved captions (and your edits to its drafts) become style examples, so new drafts sound more like you

**Staying in control**
- **Human-in-the-loop approval**: the workflow is a real pause in the graph. You can approve, edit the caption, redo one image, edit slide text, remove a slide, ask for a rewrite, or discard
- **Live phone preview** that updates as you type, with swipeable carousel slides
- **Agent activity timeline**: every step the agent takes (writing, painting each image, checking, scheduling, publishing) and every choice you make, with timings

**Planning and publishing**
- **Content calendar**: click a day to plan a post, drag a scheduled post to another day, or move and cancel it from its panel
- **Sync with Instagram**: posts you delete on Instagram are detected and marked as deleted in the dashboard
- Password login and secure cookies for when the dashboard is exposed through a tunnel or server

## How it works

```mermaid
flowchart LR
  A["Your request or uploaded images"] --> B["Draft caption and image ideas"]
  B --> C["Generate images and slide text"]
  C --> D["Review checks"]
  D --> E{{"Human approval (graph paused)"}}
  E -->|"approve"| F["Schedule"]
  E -->|"rewrite or new image"| B
  F --> G["Worker publishes to Instagram"]
```

The approval gate is enforced in code, not in a prompt. Only the schedule step can write a post to the database, and it is reachable only after the graph resumes with an approve or edit decision. The publish worker only touches posts marked `scheduled`, so the model has no path to publish on its own.

## Tech stack

| Part | What it uses |
| --- | --- |
| Workflow and approval pause | [LangGraph](https://github.com/langchain-ai/langgraph) (`interrupt()` with a SQLite checkpointer) |
| Dashboard and API | Flask, plain HTML, CSS and JavaScript |
| Captions and image understanding | Google Gemini (free tier), with a fallback list of models. Claude is supported as an alternative |
| Images | Pollinations (free). Cloudflare Workers AI and OpenAI are available as other providers |
| Image hosting | Cloudinary (free plan). Instagram needs a public URL |
| Publishing | Instagram Graph API |
| Storage | SQLite |
| Image processing | Pillow |

## Getting started

### 1. Install (Windows PowerShell)

```powershell
git clone https://github.com/<vaishnavitrathod>/Instagram-agent.git
cd Instagram-agent
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Python 3.10 or newer is recommended. On macOS or Linux, activate with `source .venv/bin/activate` and copy the file with `cp`.

### 2. Try it without any keys

In `.env`, set `FAKE_LLM=1` and `DRY_RUN=1`, then run:

```powershell
python app.py
```

Open http://127.0.0.1:5000. The agent produces placeholder text and images, and nothing is posted. This is a safe way to explore the whole flow.

### 3. Connect the real services

Fill in `.env` (see the table below), set `FAKE_LLM=0`, and keep `DRY_RUN=1` until you are happy with the flow. Set `DRY_RUN=0` to publish for real.

| Service | What you need | Where to get it |
| --- | --- | --- |
| Gemini | `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com) (free, no card) |
| Pollinations | `POLLINATIONS_API_KEY` | [enter.pollinations.ai](https://enter.pollinations.ai) (free) |
| Cloudinary | `CLOUDINARY_URL` | [cloudinary.com](https://cloudinary.com) dashboard, built as `cloudinary://API_KEY:API_SECRET@CLOUD_NAME` |
| Instagram | `IG_USER_ID`, `IG_ACCESS_TOKEN` | See below |

### Instagram credentials

1. Use an Instagram **Business or Creator** account linked to a **Facebook Page**.
2. Create an app at [developers.facebook.com](https://developers.facebook.com). While it is in development mode and you are its admin, you can publish to your own account without app review.
3. In the Graph API Explorer, generate a user token with `instagram_basic`, `instagram_content_publish`, `pages_show_list` and `pages_read_engagement`.
4. Extend it to a long-lived token in the Access Token Debugger, then request your Page's token with `PAGE_ID?fields=access_token,instagram_business_account`.
5. The `instagram_business_account` id is your `IG_USER_ID`. The Page token is your `IG_ACCESS_TOKEN`.

Tokens expire (check the expiry in the Access Token Debugger), so set a reminder to renew yours.

## Configuration

All settings live in `.env`. See `.env.example` for the full list with comments.

| Variable | Purpose | Default |
| --- | --- | --- |
| `TEXT_PROVIDER` | `gemini` or `claude` | `gemini` |
| `GEMINI_MODELS` | Comma-separated models, tried in order | see `.env.example` |
| `IMAGE_PROVIDERS` | `pollinations`, `cloudflare`, `openai`, tried in order | `pollinations` |
| `BRAND_VOICE` | Tone used in every caption | friendly |
| `BANNED_WORDS` | Words the review step flags | none |
| `DRY_RUN` | `1` = never post, print instead | `1` |
| `FAKE_LLM` | `1` = placeholder text and images, no API calls | `0` |
| `LEARN_STYLE` | `0` turns off learning from approved captions | `1` |
| `SYNC_MINUTES` | How often to check for posts deleted on Instagram (`0` = manual only) | `30` |
| `WORKER` | `0` stops the app publishing scheduled posts | `1` |
| `TEXT_FONT` | Path to a `.ttf` font for slide text | Segoe UI or DejaVu |
| `PORT` | Dashboard port | `5000` |

## Using the dashboard

1. Choose **AI image**, **AI carousel** or **My own images** in the composer, describe the post, and press **Generate post**.
2. When the post is ready, review the preview, edit the caption, slides or schedule, and press **Approve and schedule**.
3. Watch the **calendar** for upcoming posts. Drag a scheduled post to another day to reschedule it.
4. Press **Sync with Instagram** to refresh the status of posts you removed from Instagram.

The publish worker runs inside the app, so keep `python app.py` running for scheduled posts to go out.

## Making it reachable from the internet

The dashboard listens on `127.0.0.1` only. To open it from elsewhere (for example through an ngrok tunnel or a server), set these in `.env` first:

```
APP_PASSWORD=a-long-unique-password
SECRET_KEY=<random string, e.g. python -c "import secrets;print(secrets.token_hex(32))">
ALLOWED_HOSTS=127.0.0.1,localhost,your-domain.example
COOKIE_SECURE=1
```

The app refuses to start with a public host and no password. Because this dashboard can post to your Instagram account, use a strong, unique password and only serve it over HTTPS.

## Project structure

```
app.py          Flask dashboard, API and the publish worker
graph.py        LangGraph workflow: draft, images, review, approval pause, schedule
llm.py          Text generation (Gemini or Claude), with retries and model fallback
images.py       Image generation, slide text rendering, upload handling, hosting
publisher.py    Instagram publishing (single image and carousel)
igsync.py       Detects posts deleted on Instagram
activity.py     Records the agent's steps for the timeline
db.py           SQLite storage (posts, style memory, activity)
net.py          HTTP helper with retries
main.py         Basic command-line version
static/         The dashboard (index.html)
```

## Limits and notes

- Instagram needs a public **JPEG** URL for each image, which is why images are uploaded to Cloudinary first. Uploads are converted to JPEG and cropped to Instagram's allowed aspect ratios (4:5 to 1.91:1). HEIC photos must be converted first.
- Instagram limits API publishing to 25 posts per 24 hours.
- Free AI tiers get busy. The app retries and falls back to other models, but a heavy spike can still cause a failed draft, which you can retry.
- Carousel images are generated one after another, so they take a few minutes.
- Sync is paused while `DRY_RUN=1`, because nothing is really on Instagram in that mode.
- The dashboard is built for a single owner. It has no multi-user accounts.

## Troubleshooting

| Problem | Likely cause and fix |
| --- | --- |
| "All Gemini models failed" (503) | Google's free tier is busy. Wait and retry, or add more models to `GEMINI_MODELS` |
| `Instagram API 400 ... Session has expired` | The access token expired. Generate and extend a new one, update `.env`, restart |
| "Failed to fetch" in the dashboard | `python app.py` is not running |
| `No module named ...` | The virtual environment is not active. Run `.venv\Scripts\Activate.ps1` |
| Scheduled post did not go out | `DRY_RUN` is `1`, the app is not running, or the PC is asleep |
| Everything shows "deleted" after Sync | The listing failed or `IG_USER_ID` points at a different account. Check `.env` |
