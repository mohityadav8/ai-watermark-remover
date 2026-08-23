#!/usr/bin/env python3
"""ai-watermark-remover — one-command local launcher (no Docker needed).

Starts the cleaning engine and serves the web UI together, so you can just:

    python3 run_local.py

then open the URL it prints (default http://127.0.0.1:8080).

Stdlib only, same as the engine. It does two things:
  1) launches service/scripts/server.py on 127.0.0.1:8765 (loopback)
  2) serves web/ on :8080 and forwards /api/* to the engine

That means the browser and the API share one origin, so there is no CORS to
deal with — exactly like the nginx setup, but with zero install.
"""

from __future__ import annotations

import argparse
import atexit
import http.server
import os
import socketserver
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
ENGINE = ROOT / "service" / "scripts" / "server.py"

ENGINE_HOST = "127.0.0.1"
ENGINE_PORT = 8765


def start_engine() -> subprocess.Popen:
    if not ENGINE.exists():
        sys.exit(f"engine not found at {ENGINE}")
    env = dict(os.environ)
    # If you set an API key, the UI must send the same one (Settings -> API key).
    proc = subprocess.Popen(
        [sys.executable, str(ENGINE), "--host", ENGINE_HOST, "--port", str(ENGINE_PORT)],
        env=env,
    )
    atexit.register(proc.terminate)
    return proc


def wait_for_engine(timeout: float = 15.0) -> bool:
    url = f"http://{ENGINE_HOST}:{ENGINE_PORT}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


class Proxy(http.server.SimpleHTTPRequestHandler):
    """Serve web/ and forward /api/* to the engine."""

    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(WEB_DIR), **k)

    def log_message(self, *a):  # quieter console
        pass

    # ---- proxy helpers ----
    def _forward(self, method: str) -> None:
        # /api/health -> http://127.0.0.1:8765/health
        target = f"http://{ENGINE_HOST}:{ENGINE_PORT}{self.path[4:]}"
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(target, data=body, method=method)
        for h in ("Content-Type", "Authorization"):
            if h in self.headers:
                req.add_header(h, self.headers[h])
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type",
                                 resp.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:  # engine down, etc.
            msg = f'{{"ok": false, "error": "engine unreachable: {e}"}}'.encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._forward("GET")
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._forward("POST")
        self.send_error(405)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run ai-watermark-remover locally (no Docker).")
    ap.add_argument("--port", type=int, default=8080, help="web UI port (default 8080)")
    args = ap.parse_args()

    print("Starting the cleaning engine…")
    start_engine()
    if not wait_for_engine():
        sys.exit("engine did not come up on :%d — check the console above" % ENGINE_PORT)
    print("Engine is up on 127.0.0.1:%d" % ENGINE_PORT)

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", args.port), Proxy) as httpd:
        url = f"http://127.0.0.1:{args.port}"
        print("\n  ai-watermark-remover is running")
        print(f"  Open:  {url}\n")
        print("  Press Ctrl+C to stop.\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping…")


if __name__ == "__main__":
    main()
