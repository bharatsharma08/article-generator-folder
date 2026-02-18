# Article Generator — Web App

A web-based interface for generating SEO-optimised `.docx` articles using Claude AI.
Upload a CSV, enter your API key, and generate articles from anywhere in the world via a browser.

---

## Quick Start (Local)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the server
python app.py

# 3. Open in browser
# http://localhost:5000
```

---

## CSV Format

Your CSV must have these **required** columns:

| Column | Description |
|---|---|
| `Client Name` | Name of the client / brand |
| `Title` | Blog post title |
| `Keywords` | Target SEO keywords |
| `Links To Add` | URL to link back to in the CTA |
| `Status` | Set to `ACTIVE` to process the row |

**Optional columns:**

| Column | Description |
|---|---|
| `Website Link` | Client website — scraped for context if no Source Content |
| `Source Content` | Paste website text directly to avoid scraping errors |
| `Background` | Pre-written company background (auto-generated if empty) |
| `Instruction` | Article outline (auto-generated if empty) |

---

## Deploying Online (accessible from anywhere)

### Option A — Railway (Easiest, free tier available)
1. Push this repo to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Railway auto-detects Flask and deploys it
4. You get a public URL like `https://your-app.railway.app`

### Option B — Render
1. Push repo to GitHub
2. Go to [render.com](https://render.com) → New Web Service
3. Set **Start Command**: `python app.py`
4. Set environment variable `PORT=10000` (Render's default)
5. Deploy → get public URL

### Option C — Fly.io
```bash
fly launch
fly deploy
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PORT` | `5000` | Port to listen on |
| `SECRET_KEY` | random | Flask session secret |
| `FLASK_DEBUG` | `false` | Enable debug mode |

---

## Security Notes

- Your **Anthropic API key** is never stored — it is only held in memory during the generation job.
- Generated files are stored temporarily on the server in the `output/` folder.
- For production use, add authentication (e.g., a simple login page) before exposing publicly.
