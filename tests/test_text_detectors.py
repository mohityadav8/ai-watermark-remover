"""Tests for text_detectors.py (vendor/research text-watermark detectors)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "service" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import text_detectors


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "WATERMARKS_MARKLLM_SCHEME",
        "MARKLLM_DIR",
        "WATERMARKS_MARKLLM_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)


# --- MarkLLM ---------------------------------------------------------------


def test_markllm_unconfigured():
    assert text_detectors.MarkLLMTextDetector().available() is False
    report = text_detectors.MarkLLMTextDetector().detect("hello")
    assert report["available"] is False
    assert "MARKLLM_DIR" in report["error"]


def test_markllm_success(monkeypatch):
    monkeypatch.setenv("MARKLLM_DIR", "/fake/MarkLLM")
    payload = {"is_watermarked": True, "score": 4.2, "threshold": 4.0}
    monkeypatch.setattr(
        text_detectors.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout=json.dumps(payload)),
    )
    report = text_detectors.MarkLLMTextDetector().detect("hello")
    assert report["available"] is True
    assert report["is_watermarked"] is True
    assert "research harness" in report["note"]


def test_markllm_unavailable_exit3(monkeypatch):
    monkeypatch.setenv("MARKLLM_DIR", "/fake/MarkLLM")
    monkeypatch.setattr(
        text_detectors.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 3, stdout="", stderr="missing deps"),
    )
    report = text_detectors.MarkLLMTextDetector().detect("hello")
    assert report["available"] is False
    assert "missing deps" in report["error"]


def test_markllm_scheme_env(monkeypatch):
    monkeypatch.setenv("MARKLLM_DIR", "/fake/MarkLLM")
    monkeypatch.setenv("WATERMARKS_MARKLLM_SCHEME", "synthid")
    seen = {}

    def fake_run(*args, **kwargs):
        seen["argv"] = args[0]
        return subprocess.CompletedProcess(args[0], 0, stdout="{}")

    monkeypatch.setattr(text_detectors.subprocess, "run", fake_run)
    text_detectors.MarkLLMTextDetector().detect("hello")
    assert "--scheme" in seen["argv"]
    assert seen["argv"][seen["argv"].index("--scheme") + 1] == "synthid"


def test_markllm_prefers_checkout_venv(monkeypatch, tmp_path):
    upstream = tmp_path / "MarkLLM"
    if os.name == "nt":
        venv_python = upstream / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = upstream / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("")
    (upstream / "watermark").mkdir()
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["argv"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}")

    monkeypatch.setattr(text_detectors.subprocess, "run", fake_run)
    det = text_detectors.MarkLLMTextDetector(upstream_dir=str(upstream))
    assert det.available() is True
    det.detect("hello")
    assert seen["argv"][0] == str(venv_python)


def test_markllm_falls_back_to_sys_executable(monkeypatch, tmp_path):
    upstream = tmp_path / "MarkLLM"
    upstream.mkdir()
    (upstream / "watermark").mkdir()
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["argv"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}")

    monkeypatch.setattr(text_detectors.subprocess, "run", fake_run)
    det = text_detectors.MarkLLMTextDetector(upstream_dir=str(upstream))
    det.detect("hello")
    assert seen["argv"][0] == sys.executable


def test_markllm_ctor_overrides_passed_to_adapter(monkeypatch, tmp_path):
    upstream = tmp_path / "MarkLLM"
    upstream.mkdir()
    (upstream / "watermark").mkdir()
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["argv"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}")

    monkeypatch.setattr(text_detectors.subprocess, "run", fake_run)
    det = text_detectors.MarkLLMTextDetector(
        scheme="synthid",
        upstream_dir=str(upstream),
        model="opt-1.3b",
        timeout=5,
    )
    det.detect("hello")
    argv = seen["argv"]
    assert argv[argv.index("--scheme") + 1] == "synthid"
    assert argv[argv.index("--model") + 1] == "opt-1.3b"
    assert argv[argv.index("--upstream-dir") + 1] == str(upstream.resolve())


def test_markllm_ctor_available_with_override(monkeypatch, tmp_path):
    upstream = tmp_path / "MarkLLM"
    upstream.mkdir()
    det = text_detectors.MarkLLMTextDetector(upstream_dir=str(upstream))
    assert det.available() is True


def test_markllm_preexec_default_off(monkeypatch):
    monkeypatch.delenv("WATERMARKS_MARKLLM_RLIMIT_AS", raising=False)
    assert text_detectors._markllm_preexec() is None


def test_markllm_preexec_env(monkeypatch):
    if os.name != "posix":
        pytest.skip("preexec_fn is POSIX-only")
    monkeypatch.setenv("WATERMARKS_MARKLLM_RLIMIT_AS", "0x40000000")
    assert callable(text_detectors._markllm_preexec())


def test_markllm_detect_applies_rlimit(monkeypatch, tmp_path):
    if os.name != "posix":
        pytest.skip("preexec_fn is POSIX-only")
    upstream = tmp_path / "MarkLLM"
    upstream.mkdir()
    (upstream / "watermark").mkdir()
    monkeypatch.setenv("WATERMARKS_MARKLLM_RLIMIT_AS", "1073741824")
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["preexec_fn"] = kwargs.get("preexec_fn")
        return subprocess.CompletedProcess(cmd, 0, stdout='{"available": true}')

    monkeypatch.setattr(text_detectors.subprocess, "run", fake_run)
    det = text_detectors.MarkLLMTextDetector(upstream_dir=str(upstream))
    report = det.detect("hello")
    assert report["available"] is True
    assert callable(captured["preexec_fn"])


def _spin_worker_server(response):
    import socketserver
    import threading

    class _H(socketserver.BaseRequestHandler):
        def handle(self):
            f = self.request.makefile("r", encoding="utf-8")
            f.readline()
            self.request.sendall((json.dumps(response) + "\n").encode("utf-8"))

    class _S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    srv = _S(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_markllm_detector_uses_worker_port(monkeypatch, tmp_path):
    upstream = tmp_path / "MarkLLM"
    upstream.mkdir()
    srv = _spin_worker_server({"ok": True, "is_watermarked": True, "score": 2.0, "threshold": 0.5})
    monkeypatch.setenv("WATERMARKS_MARKLLM_PORT", str(srv.server_address[1]))
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="{}")

    monkeypatch.setattr(text_detectors.subprocess, "run", fake_run)
    det = text_detectors.MarkLLMTextDetector(upstream_dir=str(upstream))
    report = det.detect("hello")
    srv.shutdown()
    srv.server_close()
    assert report["available"] is True
    assert report["is_watermarked"] is True
    assert report["score"] == 2.0
    assert seen == []  # no cold-start subprocess
    assert "worker" in report["note"]


def test_markllm_detector_worker_fallback_to_subprocess(monkeypatch, tmp_path):
    upstream = tmp_path / "MarkLLM"
    upstream.mkdir()
    monkeypatch.setenv("WATERMARKS_MARKLLM_PORT", "1")  # nothing listening
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"is_watermarked": True, "score": 1.0})
        )

    monkeypatch.setattr(text_detectors.subprocess, "run", fake_run)
    det = text_detectors.MarkLLMTextDetector(upstream_dir=str(upstream))
    report = det.detect("hello")
    assert report["available"] is True
    assert report["is_watermarked"] is True
    assert len(calls) == 1  # fell back to the subprocess


def test_run_all_text_detectors_can_exclude_markllm(monkeypatch):
    monkeypatch.setattr(
        text_detectors,
        "MarkLLMTextDetector",
        lambda: pytest.fail("must not construct MarkLLM when excluded"),
    )
    reports = text_detectors.run_all_text_detectors("hello", include_markllm=False)
    assert len(reports) == 2  # gumbel + claude placeholder
    assert {r["detector"] for r in reports} == {"gumbel", "claude-text"}


def test_run_all_text_detectors_injects_markllm_instance(monkeypatch, tmp_path):
    upstream = tmp_path / "MarkLLM"
    upstream.mkdir()
    det = text_detectors.MarkLLMTextDetector(upstream_dir=str(upstream), scheme="synthid")
    seen: list = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="{}")

    monkeypatch.setattr(text_detectors.subprocess, "run", fake_run)
    text_detectors.run_all_text_detectors("hello", markllm=det)
    markllm_cmd = next(c for c in seen if "--scheme" in c)
    assert markllm_cmd[markllm_cmd.index("--scheme") + 1] == "synthid"


# --- Claude placeholder ----------------------------------------------------


def test_claude_placeholder():
    det = text_detectors.ClaudeTextDetector()
    assert det.available() is False
    report = det.detect("hello")
    assert report["available"] is False
    assert "WATERMARKS_CLAUDE_API_KEY" in report["error"]


# --- Registry --------------------------------------------------------------


def test_detector_status_keys():
    status = text_detectors.detector_status()
    assert set(status) == {"markllm", "gumbel", "claude-text"}


def test_run_all_text_detectors_length():
    reports = text_detectors.run_all_text_detectors("hello")
    assert len(reports) == 3  # markllm + gumbel + claude placeholder
    assert all("detector" in r for r in reports)


def test_run_text_detectors_filters_unavailable():
    reports = text_detectors.run_text_detectors("hello")
    assert reports == []
