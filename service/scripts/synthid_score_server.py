#!/usr/bin/env python3
"""Tiny stdlib HTTP sidecar exposing the reverse-SynthID pixel scorer.

Runs inside the local-only wr-synthid heavy image so the published core
image never bundles the non-commercial reverse-SynthID code. The core
service calls this sidecar for SynthID image scoring when
WATERMARKS_SYNTHID_SCORER_URL is set (see compose.yaml / .env.example).

Endpoints:
    GET  /health  -> {"ok": true, "version": ...}
    POST /score   -> {"file": <base64>} -> score_synthid payload

Hardening mirrors server.py: optional bearer key, input size caps,
unprivileged user, read-only rootfs with a /tmp tmpfs. Intended for the
compose network or a trusted network only.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import sys
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from score_synthid import score_file

VERSION = os.environ.get("WATERMARKS_SYNTHID_SERVER_VERSION", "dev")

# Mirror common.MAX_INPUT_BYTES (env-overridable) with the base64 envelope
# headroom. Read at import; the sidecar image does not copy common.py, so the
# default is repeated here.
MAX_INPUT_BYTES = int(os.environ.get("WATERMARKS_MAX_INPUT_BYTES", str(256 << 20)))
MAX_BODY_BYTES = MAX_INPUT_BYTES + (MAX_INPUT_BYTES >> 1)

API_KEY = os.environ.get("WATERMARKS_SYNTHID_SCORER_API_KEY", "").strip()
MODEL = os.environ.get("WATERMARKS_SYNTHID_MODEL", "").strip() or None


def _json_ok(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = f"ai-watermark-remover-synthid/{VERSION}"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}", file=sys.stderr)

    def _authorized(self) -> bool:
        if not API_KEY:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {API_KEY}"

    def _read_json(self) -> dict[str, Any] | None:
        raw = self.headers.get("Content-Length")
        if raw is None or not raw.isdigit():
            return None
        length = int(raw)
        if length > MAX_BODY_BYTES:
            return None
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, OSError):
            return None
        return body if isinstance(body, dict) else None

    def _respond(self, status: int, payload: dict[str, Any]) -> None:
        data = _json_ok(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if not self._authorized():
            self._respond(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return
        if urlparse(self.path).path == "/health":
            self._respond(HTTPStatus.OK, {"ok": True, "version": VERSION})
        else:
            self._respond(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if not self._authorized():
            self._respond(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return
        if urlparse(self.path).path != "/score":
            self._respond(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        body = self._read_json()
        if body is None:
            raw_len = self.headers.get("Content-Length")
            oversized = raw_len is not None and raw_len.isdigit() and int(raw_len) > MAX_BODY_BYTES
            self._respond(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE if oversized else HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "invalid request body"},
            )
            return

        raw = body.get("file")
        if not isinstance(raw, str):
            self._respond(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "missing 'file' field"})
            return
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            self._respond(
                HTTPStatus.BAD_REQUEST, {"ok": False, "error": "'file' is not valid base64"}
            )
            return
        if len(data) > MAX_INPUT_BYTES:
            self._respond(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "file too large"}
            )
            return

        with tempfile.TemporaryDirectory(prefix="wm-synthid-") as tmp:
            path = Path(tmp) / "input.png"
            try:
                path.write_bytes(data)
            except OSError as e:
                self._respond(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(e)})
                return
            code, payload = score_file(path, model=MODEL)

        if code == 0 and payload is not None:
            self._respond(HTTPStatus.OK, payload)
        elif code == 2:
            self._respond(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "could not load image"})
        else:
            # exit 1 (runtime error) or 3 (unavailable) -> fail-soft payload,
            # matching the shape image_meta.run_synthid_score expects.
            self._respond(
                HTTPStatus.OK,
                {"available": False, "error": "scorer unavailable (see sidecar stderr)"},
            )


def main() -> int:
    global API_KEY  # noqa: PLW0603 — CLI overrides env
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default=os.environ.get("WATERMARKS_SYNTHID_SERVER_HOST", "127.0.0.1"))
    p.add_argument(
        "--port", type=int, default=int(os.environ.get("WATERMARKS_SYNTHID_SERVER_PORT", "8766"))
    )
    p.add_argument("--api-key", default=API_KEY, help="require this bearer token (default: none)")
    args = p.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"warning: binding {args.host} — intended for a trusted network only", file=sys.stderr
        )
    API_KEY = args.api_key
    print(f"synthid scorer sidecar {VERSION} on http://{args.host}:{args.port}", file=sys.stderr)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
