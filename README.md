# Organic Content Builder

AI-powered video pipeline for short-form social media (TikTok/Reels style). Automates the creation of "reaction" or "stitch" style videos.

## The Flow (5 steps)

1. **Download** — You give it an Instagram reel URL. It downloads the video using `yt-dlp`.
2. **Transcribe** — It runs OpenAI Whisper locally to get word-level timestamps of what's being said in the reel.
3. **AI Selection (Claude)** — It sends the transcript + your content library to Claude Sonnet, which picks:
   - **Cut point** — The best moment (within the first ~8 seconds) to cut the hook. It looks for the most emotionally loaded moment — frustration, desperation, vulnerability — and cuts right after a complete thought to leave the viewer wanting more.
   - **Body clip** — Which of your pre-filmed response clips best matches what the person said.
   - **CTA** — Optionally appends a call-to-action clip (20% chance by default).
4. **Normalize** — Re-encodes all clips to a consistent 1080×1920 @ 30fps format.
5. **Stitch** — Concatenates hook + body (+ optional CTA) into a final `.mp4`.

## Content Library

You pre-film a set of body clips and CTAs, then define them in JSON:

- **`content/bodies.json`** — Your response clips. Each has a tone, type, transcript, and `best_for` description so Claude knows when to use it.
- **`content/ctas.json`** — Short end-cards like "Follow for more AI job tools" or "Link in bio."

The actual `.mp4` files go in `content/bodies/` and `content/ctas/`.

## Usage

### CLI
```bash
python pipeline.py https://instagram.com/reel/xyz
python pipeline.py https://instagram.com/reel/xyz --cta-chance 0.5 --max-cut 6
python pipeline.py https://instagram.com/reel/xyz --dry-run  # just see what AI picks
```

### Web UI
```bash
pip install -r requirements.txt
python server.py
# Open http://localhost:8000
```

## In Short

You find reels of people struggling with job searching, the script downloads them, figures out the best "hook" moment, pairs it with your pre-filmed response clip, and spits out a ready-to-post video. It's a content factory for organic social media marketing.

## Setup

```bash
pip install -r requirements.txt
```

**Environment:** `ANTHROPIC_API_KEY` must be set.

**System deps:** `ffmpeg` and `yt-dlp` must be on PATH.
