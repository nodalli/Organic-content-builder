# Claude Code Prompt — Create Organic-content-builder Repo

Copy and paste this into Claude Code:

---

Create a new GitHub repo called `Organic-content-builder` under the `nodalli` org (public). Then:

1. `gh repo create nodalli/Organic-content-builder --public --description "AI-powered video content pipeline for short-form social media"`
2. Clone it locally
3. Copy these files from `~/nodalli-web-app-2/scripts/video-pipeline/` into the new repo root:
   - `pipeline.py`
   - `requirements.txt`
   - `content/bodies.json`
   - `content/ctas.json`
   - `content/bodies/.gitkeep`
   - `content/ctas/.gitkeep`
4. Add a `.gitignore` with:
   ```
   __pycache__/
   *.pyc
   .env
   output*.mp4
   content/bodies/*.mp4
   content/ctas/*.mp4
   !content/bodies/.gitkeep
   !content/ctas/.gitkeep
   ```
5. Commit with message "Initial commit: AI video content pipeline"
6. Push to main
