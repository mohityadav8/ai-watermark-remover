# Deployment: CLI + API in Docker

Status: idea / proposed
Date: 2026-08-14
Branch context: `refactor/format-dispatch`

## Goal

Simplify deployment of ai-watermark-remover by shipping a Docker container that
offers both a **CLI** and a persistent **HTTP API**, self-hosted via local
image builds. Primary motivation: cut host setup friction (Python version,
exiftool/qpdf/c2patool, OS drift) and expose the tool to non-Python consumers
through a shared multi-user service.

## Decisions (confirmed with author)

- Scope: **all capabilities in v1** — core (Layer A + file metadata), Layer B
  rewrite, and GPU pixel removal (CtrlRegen).
- Distribution: **all local builds** (no registry publishing), preserving the
  repo's license-safe rule of never bundling restricted upstream code.
- CLI + API refactor land **together** as one change.
- Auth: **single shared Bearer key** from env in v1 (per-consumer keys later).

## Architecture — 3 images

| Image | Contents | Mode |
| --- | --- | --- |
| `ai-watermark-remover-core` | Digest-pinned `python:3.14-slim` + exiftool/qpdf/c2patool + core scripts + FastAPI server | Default: **CLI** (`docker run wm-core clean_file.py ...`). `serve` subcommand: **HTTP API** |
| `ai-watermark-remover-ctrlregen` | Existing GPU image (unchanged, local build, license-safe) | **Worker** for async pixel-removal jobs |
| `ai-watermark-remover-markllm` / synthid | Existing optional images | **Out of the service**. MarkLLM is a verification harness, not a user-facing capability; SynthID scorer is an optional sidecar later |

Rationale: a single all-in-one image would be a GPU-dependent megaimage and
would collide with the never-bundle-restricted-upstream rule. The heavy
backends stay separate sidecars dispatched by the API.

## Phases

### Phase 0 — Callable core refactor (highest risk)

Split argv parsing from business logic in the routers so the API can run them
in-process:

- `inspect_file.py`, `clean_file.py`, `inspect_text.py`, `clean_text.py`,
  `rewrite_text.py`, `inspect_image.py`, `clean_image.py`, `audit_dir.py`,
  `audit_website.py`
- Expose a stable interface, e.g. `clean_bytes(data, filename, opts) -> (report, out_bytes)`
- Reuse `format_dispatch.py` classification and `common.py` guards
  (byte caps, binary sniff, safe writes)
- **Invariant: CLI behavior byte-for-byte identical**; the 60+ existing tests
  are the safety net.

### Phase 1 — Core image

`Dockerfile.core` in the existing hardening style: digest-pinned base,
unprivileged user, pinned pip, apt install exiftool/qpdf/c2patool. Entrypoint
dispatches on first arg (`serve` vs script name). Makefile targets:
`docker-core-build`, `docker-core-help`, `serve-core`.

### Phase 2 — API server (FastAPI + uvicorn, in the core image)

- `POST /v1/inspect` — multipart file -> JSON report (findings + confidence)
- `POST /v1/clean` — multipart file -> cleaned file streamed back
- `POST /v1/rewrite` — text + backend config -> rewritten text; loopback-only
  default, remote endpoints via server env only (never per-request)
- `POST /v1/clean/pixel` — async: SQLite job record -> returns `job_id`; worker
  picks it up
- `GET /v1/jobs/{id}`, `GET /v1/health` (reports which optional backends are reachable)
- Security:
  - Single shared `Bearer` key from env (mirrors the env-only-key rule)
  - Files always arrive as upload streams into per-request temp dirs — never
    client-supplied filesystem paths, so the symlink/safe-write protections hold
  - Reuse `common.py` byte caps per request
  - Rate limiting; documented TLS termination via reverse proxy
- Jobs in SQLite on a shared volume; worker polls the DB (no new protocol; GPU
  work serializes inherently).

### Phase 3 — Compose + docs

`docker-compose.yml` (core + ctrlregen worker, shared volume for jobs/model
cache), README section (quickstart, auth, security notes, CLI-mode examples),
Makefile `up`/`down`.

### Phase 4 — Security review

- SSRF (rewrite backend, audit_website)
- Upload size caps
- Key handling (env-only, never logged)
- Concurrency/thread-pool bounds
- Worker crash recovery for in-flight jobs

## Verification

- `make test` (refactor)
- `make smoke` (CLI unchanged)
- New tests: API endpoints via FastAPI test client, auth rejection, cap
  enforcement, async job lifecycle with a mocked worker, `docker run wm-core ...`
  smoke against fixtures.

## Open items

- MarkLLM excluded from the service (flag if disagreed)
- CtrlRegen worker communication = shared volume + SQLite polling (simple, no new infra)
- SynthID scorer = optional future sidecar, not v1
