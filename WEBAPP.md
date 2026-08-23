# Web app — frontend + backend in one repo

This repository is the upstream **ai-watermark-remover** engine (a Python
cleaning service + agent skills) with a **web application layer added on top**,
so the whole thing — UI and backend — runs from one repo with one command.

Nothing in the original engine was changed. The new pieces are:

```
web/                      ← NEW: the browser UI (no build step)
  index.html                markup
  styles.css                design system (forensic "signal" theme)
  app.js                    talks to the engine's HTTP API

run_local.py              ← NEW: one-command local run (no Docker)

deploy/                   ← NEW: one-command full-stack deployment
  docker-compose.yml        runs the UI + engine together
  Dockerfile.web            nginx image serving web/ + proxying /api
  nginx.conf                reverse proxy config (no CORS needed)
  .env.example              the one place to set your API key
  DEPLOYMENT.md             local + AWS deployment guide

service/                  ← UNCHANGED upstream engine (the backend)
skills/ tests/ …          ← UNCHANGED upstream
```

## What it does

Upload one or many files. Two modes:

- **Inspect** — report AI provenance marks (invisible Unicode, C2PA / EXIF /
  XMP metadata, doc properties, stylometry score) **without changing the file.**
- **Clean** — strip those marks and hand back the cleaned file to download. The
  document body is preserved; only the marks come out.

Supported: text, Markdown, HTML, PNG, JPEG, WebP, AVIF, HEIC, BMP, GIF, TIFF,
SVG, PDF, DOCX, XLSX, PPTX, EPUB, ODT, and common audio/video containers —
whatever the engine supports, the UI can send.

## Architecture

```
Browser ─▶ nginx (wr-web)
             ├── /            serves web/ (static UI)
             └── /api/*  ───▶ Python engine (wr-core :8765)
```

The UI and API share one origin through nginx, so there is no CORS to deal
with, and the engine is never exposed to the internet directly — only nginx is.

The UI calls four endpoints on the engine: `GET /health`,
`GET /capabilities`, `POST /inspect/batch`, `POST /clean/batch`. The engine's
full API (single-file `/inspect` `/clean` `/detect`, `/openapi.json`) is
documented in the main [README](README.md).

## Quickstart

### Option 1 — no Docker (simplest, just Python)

Requires Python 3.10+ (the engine needs it anyway). From the repo root:

```bash
python3 run_local.py
```

Then open **http://127.0.0.1:8080**. That's it — `run_local.py` starts the
cleaning engine and serves the UI together, forwarding `/api/*` to the engine
so there's no CORS to configure.

### Option 2 — Docker (for servers / AWS)

```bash
cd deploy
cp .env.example .env      # optional — set WATERMARKS_SERVER_API_KEY to lock it down
docker compose up --build -d
```

Open **http://localhost:8080**.

Deploying to AWS and where every key/setting lives:
**[deploy/DEPLOYMENT.md](deploy/DEPLOYMENT.md).**

## API key — the short version

The cleaning is fully local; **no third-party AI key is required.** The only
key is an *optional* bearer token that locks the service down:

1. `deploy/.env` → `WATERMARKS_SERVER_API_KEY=<random string>`
2. UI → **Settings** → paste the same value.

Leave it empty for an open service (fine on a private box, not on the public
internet). Full details in [deploy/DEPLOYMENT.md](deploy/DEPLOYMENT.md).

## Responsible use

For content you **own or are authorized to process** — privacy and hygiene, not
academic fraud or false "human-written" claims. See
[`skills/remove-ai-marks/references/ethics.md`](skills/remove-ai-marks/references/ethics.md).
