#!/usr/bin/env python3
"""
Web server for the video content pipeline.

Usage:
    python server.py
    # Open http://localhost:8000
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

CONTENT_DIR = Path(__file__).parent / "content"
OUTPUT_DIR = Path(__file__).parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

app = FastAPI()

# In-memory job store
jobs: dict[str, dict] = {}


class PipelineRequest(BaseModel):
    url: str
    max_cut: float = 8.0
    cta_chance: float = 0.2
    dry_run: bool = False


# ---- Content library endpoints ----

@app.get("/api/content")
def get_content():
    with open(CONTENT_DIR / "bodies.json") as f:
        bodies = json.load(f)
    with open(CONTENT_DIR / "ctas.json") as f:
        ctas = json.load(f)
    return {"bodies": bodies, "ctas": ctas}


# ---- Pipeline endpoints ----

@app.post("/api/run")
async def start_pipeline(req: PipelineRequest):
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "queued", "steps": [], "result": None, "error": None}
    asyncio.create_task(_run_pipeline(job_id, req))
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
async def stream_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")

    async def event_stream():
        last_len = 0
        while True:
            job = jobs[job_id]
            steps = job["steps"]
            # Send any new steps
            while last_len < len(steps):
                data = json.dumps(steps[last_len])
                yield f"data: {data}\n\n"
                last_len += 1
            if job["status"] in ("done", "error"):
                final = {"type": "final", "status": job["status"],
                         "result": job["result"], "error": job["error"]}
                yield f"data: {json.dumps(final)}\n\n"
                break
            await asyncio.sleep(0.3)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/outputs/{filename}")
async def get_output(filename: str):
    path = OUTPUT_DIR / filename
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="video/mp4")


# ---- Pipeline runner ----

async def _run_pipeline(job_id: str, req: PipelineRequest):
    job = jobs[job_id]
    job["status"] = "running"

    try:
        with tempfile.TemporaryDirectory() as workdir:
            # Step 1: Download
            _step(job, 1, "Downloading reel...", "running")
            reel_path = os.path.join(workdir, "reel.mp4")
            await _async_run([
                "yt-dlp", "--no-warnings",
                "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "--merge-output-format", "mp4",
                "-o", reel_path, req.url,
            ])
            _step(job, 1, "Downloaded", "done")

            # Step 2: Transcribe
            _step(job, 2, "Transcribing audio...", "running")
            words = await asyncio.to_thread(_transcribe_sync, reel_path)
            _step(job, 2, f"Transcribed {len(words)} words", "done")

            # Step 3: AI Selection
            _step(job, 3, "AI picking cut point & body...", "running")
            with open(CONTENT_DIR / "bodies.json") as f:
                bodies = json.load(f)
            with open(CONTENT_DIR / "ctas.json") as f:
                ctas = json.load(f)
            picks = await asyncio.to_thread(
                _ai_select_sync, words, bodies, ctas, req.max_cut, req.cta_chance
            )
            _step(job, 3, f"Cut at {picks['cut_time']}s | Body: {picks['body_id']}", "done")

            if req.dry_run:
                job["result"] = {"picks": picks, "dry_run": True}
                job["status"] = "done"
                return

            # Step 4: Normalize
            _step(job, 4, "Trimming & normalizing clips...", "running")
            trimmed = os.path.join(workdir, "hook.mp4")
            await _async_run([
                "ffmpeg", "-y", "-i", reel_path, "-t", str(picks["cut_time"]),
                "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart",
                trimmed, "-loglevel", "warning",
            ])

            clips = [trimmed]
            body_file = str(CONTENT_DIR / next(
                b["file"] for b in bodies if b["id"] == picks["body_id"]
            ))
            clips.append(body_file)
            if picks.get("cta_id"):
                cta_file = str(CONTENT_DIR / next(
                    c["file"] for c in ctas if c["id"] == picks["cta_id"]
                ))
                if os.path.exists(cta_file):
                    clips.append(cta_file)

            normalized = []
            for i, clip in enumerate(clips):
                norm = os.path.join(workdir, f"norm_{i}.mp4")
                await _async_run([
                    "ffmpeg", "-y", "-i", clip,
                    "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart",
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:-1:-1:color=black",
                    "-r", "30", norm, "-loglevel", "warning",
                ])
                normalized.append(norm)
            _step(job, 4, f"Normalized {len(normalized)} clips", "done")

            # Step 5: Stitch
            _step(job, 5, "Stitching final video...", "running")
            concat_file = os.path.join(workdir, "concat.txt")
            with open(concat_file, "w") as f:
                for p in normalized:
                    f.write(f"file '{p}'\n")

            output_name = f"output_{job_id}.mp4"
            output_path = str(OUTPUT_DIR / output_name)
            await _async_run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", concat_file, "-c", "copy",
                output_path, "-loglevel", "warning",
            ])
            _step(job, 5, "Done!", "done")

            job["result"] = {
                "picks": picks,
                "output": output_name,
                "dry_run": False,
            }
            job["status"] = "done"

    except Exception as e:
        job["error"] = str(e)
        job["status"] = "error"
        _step(job, 0, f"Error: {e}", "error")


def _step(job, num, message, status):
    job["steps"].append({"step": num, "message": message, "status": status})


async def _async_run(cmd):
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd[:2])}... — {stderr.decode()[-500:]}")


def _transcribe_sync(video_path: str) -> list[dict]:
    import whisper
    model = whisper.load_model("base")
    result = model.transcribe(video_path, word_timestamps=True)
    words = []
    for segment in result["segments"]:
        for word in segment.get("words", []):
            words.append({
                "start": round(word["start"], 2),
                "end": round(word["end"], 2),
                "text": word["word"].strip(),
            })
    return words


def _ai_select_sync(words, bodies, ctas, max_cut, cta_chance):
    import random
    import anthropic

    transcript_str = " ".join(f"[{w['start']}s] {w['text']}" for w in words)
    body_descriptions = "\n".join(
        f"- id: {b['id']}\n  tone: {b['tone']}\n  type: {b['type']}\n"
        f"  transcript: \"{b['transcript']}\"\n  best_for: {b['best_for']}"
        for b in bodies
    )
    cta_descriptions = "\n".join(
        f"- id: {c['id']}\n  transcript: \"{c['transcript']}\"" for c in ctas
    )
    include_cta = random.random() < cta_chance

    prompt = f"""You are editing short-form video content for social media.

HOOK VIDEO TRANSCRIPT (with word-level timestamps):
{transcript_str}

This is a clip of a struggling student/job-seeker talking about their difficulties.
I need you to pick the perfect cut point within the first {max_cut} seconds.

CUT POINT RULES:
- Must be within the first {max_cut} seconds
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
    return json.loads(message.content[0].text.strip())


# ---- Static files ----

app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
