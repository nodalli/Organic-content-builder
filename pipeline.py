#!/usr/bin/env python3
"""
Video Content Pipeline

Downloads an Instagram reel, transcribes it, uses AI to pick the best
cut point and body clip, optionally appends a CTA, and stitches
everything into a final video.

Usage:
    python pipeline.py <INSTAGRAM_URL>
    python pipeline.py <INSTAGRAM_URL> --cta-chance 0.2 --max-cut 8

Requirements:
    pip install anthropic openai yt-dlp

Environment:
    ANTHROPIC_API_KEY must be set
    OPENAI_API_KEY must be set (for Whisper transcription)
"""

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

CONTENT_DIR = Path(__file__).parent / "content"


# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------

def check_dependencies():
    missing = []
    for cmd in ["ffmpeg", "yt-dlp"]:
        if not subprocess.shutil.which(cmd):
            missing.append(cmd)
    try:
        import openai  # noqa: F401
    except ImportError:
        missing.append("openai (pip install openai)")
    try:
        import anthropic  # noqa: F401
    except ImportError:
        missing.append("anthropic (pip install anthropic)")

    if missing:
        print("Missing dependencies:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY environment variable is not set.")
        sys.exit(1)

    if not os.environ.get("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY environment variable is not set.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Step 1: Download the reel
# ---------------------------------------------------------------------------

def download_reel(url: str, output_path: str) -> str:
    print(f"[1/5] Downloading reel...")
    subprocess.run(
        [
            "yt-dlp",
            "--no-warnings",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "-o", output_path,
            url,
        ],
        check=True,
    )
    print(f"      Downloaded to {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Step 2: Transcribe with Whisper
# ---------------------------------------------------------------------------

def transcribe(video_path: str) -> list[dict]:
    """Returns list of {'start': float, 'end': float, 'text': str} segments."""
    from openai import OpenAI

    print("[2/5] Transcribing audio...")
    client = OpenAI()
    with open(video_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )

    words = []
    for word in result.words:
        words.append({
            "start": round(word["start"], 2),
            "end": round(word["end"], 2),
            "text": word["word"].strip(),
        })

    print(f"      Transcribed {len(words)} words")
    return words


# ---------------------------------------------------------------------------
# Step 3: AI picks cut point + best body
# ---------------------------------------------------------------------------

def ai_select(
    transcript_words: list[dict],
    bodies: list[dict],
    ctas: list[dict],
    max_cut_seconds: float,
    cta_chance: float,
) -> dict:
    """
    Asks Claude to pick:
      - cut_time: where to cut the hook (within first max_cut_seconds)
      - body_id: which body clip to use
      - cta_id: which CTA to append (or null)

    Returns dict with those keys.
    """
    import anthropic

    print("[3/5] Asking AI to pick cut point and body...")

    # Build transcript text with timestamps
    transcript_str = " ".join(
        f"[{w['start']}s] {w['text']}" for w in transcript_words
    )

    body_descriptions = "\n".join(
        f"- id: {b['id']}\n  tone: {b['tone']}\n  type: {b['type']}\n"
        f"  transcript: \"{b['transcript']}\"\n  best_for: {b['best_for']}"
        for b in bodies
    )

    cta_descriptions = "\n".join(
        f"- id: {c['id']}\n  transcript: \"{c['transcript']}\""
        for c in ctas
    )

    include_cta = random.random() < cta_chance

    prompt = f"""You are editing short-form video content for social media.

HOOK VIDEO TRANSCRIPT (with word-level timestamps):
{transcript_str}

This is a clip of a struggling student/job-seeker talking about their difficulties.
I need you to pick the perfect cut point within the first {max_cut_seconds} seconds.

CUT POINT RULES:
- Must be within the first {max_cut_seconds} seconds
- Cut right AFTER a complete thought — not mid-sentence
- Pick the moment with the most emotional weight: frustration, desperation, vulnerability
- The cut should leave the viewer wanting to know the answer/solution

AVAILABLE BODY CLIPS (these are my response clips that play after the hook):
{body_descriptions}

Pick the body clip that best responds to what the person said in the hook.

{"AVAILABLE CTAs (append one of these at the end):" if include_cta else "NO CTA for this video."}
{cta_descriptions if include_cta else ""}

Respond with ONLY valid JSON, no markdown:
{{
  "cut_time": <float seconds>,
  "cut_reasoning": "<1 sentence why>",
  "body_id": "<id>",
  "body_reasoning": "<1 sentence why>",
  "cta_id": {"<id> or null" if include_cta else "null"}
}}"""

    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()
    result = json.loads(response_text)

    print(f"      Cut at {result['cut_time']}s — {result['cut_reasoning']}")
    print(f"      Body: {result['body_id']} — {result['body_reasoning']}")
    if result.get("cta_id"):
        print(f"      CTA: {result['cta_id']}")
    else:
        print("      CTA: none")

    return result


# ---------------------------------------------------------------------------
# Step 4: Trim + stitch with ffmpeg
# ---------------------------------------------------------------------------

def trim_video(input_path: str, output_path: str, duration: float):
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", input_path,
            "-t", str(duration),
            "-c:v", "libx264", "-c:a", "aac",
            "-movflags", "+faststart",
            output_path,
            "-loglevel", "warning",
        ],
        check=True,
    )


def normalize_video(input_path: str, output_path: str):
    """Re-encode to consistent format for clean concatenation."""
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", input_path,
            "-c:v", "libx264", "-c:a", "aac",
            "-movflags", "+faststart",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:-1:-1:color=black",
            "-r", "30",
            output_path,
            "-loglevel", "warning",
        ],
        check=True,
    )


def stitch_videos(video_paths: list[str], output_path: str, workdir: str):
    """Normalize all clips and concatenate them."""
    print("[4/5] Stitching videos...")

    normalized = []
    for i, vpath in enumerate(video_paths):
        norm_path = os.path.join(workdir, f"norm_{i}.mp4")
        print(f"      Normalizing clip {i + 1}/{len(video_paths)}...")
        normalize_video(vpath, norm_path)
        normalized.append(norm_path)

    concat_file = os.path.join(workdir, "concat.txt")
    with open(concat_file, "w") as f:
        for p in normalized:
            f.write(f"file '{p}'\n")

    print("[5/5] Exporting final video...")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_file,
            "-c", "copy",
            output_path,
            "-loglevel", "warning",
        ],
        check=True,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Video content pipeline")
    parser.add_argument("url", help="Instagram reel URL")
    parser.add_argument("--output", default="output.mp4", help="Output file path")
    parser.add_argument("--max-cut", type=float, default=8, help="Max cut time in seconds (default: 8)")
    parser.add_argument("--cta-chance", type=float, default=0.2, help="Probability of appending a CTA (default: 0.2 = 20%%)")
    parser.add_argument("--dry-run", action="store_true", help="Transcribe and get AI picks without stitching")
    args = parser.parse_args()

    check_dependencies()

    # Load content library
    with open(CONTENT_DIR / "bodies.json") as f:
        bodies = json.load(f)
    with open(CONTENT_DIR / "ctas.json") as f:
        ctas = json.load(f)

    # Validate that body/CTA video files exist
    for b in bodies:
        path = CONTENT_DIR / b["file"]
        if not path.exists():
            print(f"Warning: Body clip not found: {path}")
            print("         Add your filmed clips to the content/bodies/ directory.")

    for c in ctas:
        path = CONTENT_DIR / c["file"]
        if not path.exists():
            print(f"Warning: CTA clip not found: {path}")

    with tempfile.TemporaryDirectory() as workdir:
        # Download
        reel_path = os.path.join(workdir, "reel.mp4")
        download_reel(args.url, reel_path)

        # Transcribe
        words = transcribe(reel_path)

        # AI selection
        picks = ai_select(words, bodies, ctas, args.max_cut, args.cta_chance)

        if args.dry_run:
            print("\n--- DRY RUN RESULT ---")
            print(json.dumps(picks, indent=2))
            return

        # Trim hook
        trimmed_path = os.path.join(workdir, "hook.mp4")
        trim_video(reel_path, trimmed_path, picks["cut_time"])

        # Gather clips to stitch
        clips = [trimmed_path]

        body_file = CONTENT_DIR / next(
            b["file"] for b in bodies if b["id"] == picks["body_id"]
        )
        if not body_file.exists():
            print(f"Error: Body clip not found: {body_file}")
            sys.exit(1)
        clips.append(str(body_file))

        if picks.get("cta_id"):
            cta_file = CONTENT_DIR / next(
                c["file"] for c in ctas if c["id"] == picks["cta_id"]
            )
            if cta_file.exists():
                clips.append(str(cta_file))
            else:
                print(f"Warning: CTA clip not found: {cta_file}, skipping CTA")

        # Stitch
        stitch_videos(clips, args.output, workdir)

        print(f"\nDone! Final video: {args.output}")


if __name__ == "__main__":
    main()
