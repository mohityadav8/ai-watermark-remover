"""Tests for how c2patool output is interpreted by run_optional_tools."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "service" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import image_meta

MANIFEST_OUTPUT = """{
  "active_manifest": "urn:c2pa:0000",
  "manifests": {
    "urn:c2pa:0000": {
      "claim_generator": "some_tool/1.0",
      "assertions": [{"label": "c2pa.actions"}]
    }
  }
}
"""


class _Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_c2patool(monkeypatch, output: str, returncode: int, on_stderr: bool = False):
    """Pretend c2patool exists and emits `output`; hide every other tool."""

    def fake_which(cmd: str):
        return "/fake/bin/c2patool" if cmd == "c2patool" else None

    def fake_run(cmd, **kwargs):
        if on_stderr:
            return _Completed(returncode, stderr=output)
        return _Completed(returncode, stdout=output)

    monkeypatch.setattr(image_meta, "which", fake_which)
    monkeypatch.setattr(image_meta.subprocess, "run", fake_run)


def test_no_claim_found_is_not_a_manifest(monkeypatch, tmp_path):
    """Absence of a manifest must not read as a hit.

    c2patool reports a missing manifest as "Error: No claim found", which
    contains the substring "claim"; a positive branch that is not vetoed by
    the negative markers flags every clean asset as carrying C2PA.
    """
    _fake_c2patool(monkeypatch, "Error: No claim found\n", 1, on_stderr=True)
    path = tmp_path / "clean.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")

    tools = image_meta.run_optional_tools(path)

    assert tools["c2patool"]["available"] is True
    assert tools["c2patool"]["has_manifest"] is False


def test_no_jumbf_data_is_not_a_manifest(monkeypatch, tmp_path):
    _fake_c2patool(monkeypatch, "No JUMBF data found\n", 1, on_stderr=True)
    path = tmp_path / "clean.jpg"
    path.write_bytes(b"\xff\xd8\xff")

    tools = image_meta.run_optional_tools(path)

    assert tools["c2patool"]["has_manifest"] is False


def test_real_manifest_is_detected(monkeypatch, tmp_path):
    """The negative guard must not suppress genuine manifests."""
    _fake_c2patool(monkeypatch, MANIFEST_OUTPUT, 0)
    path = tmp_path / "signed.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")

    tools = image_meta.run_optional_tools(path)

    assert tools["c2patool"]["has_manifest"] is True


def test_missing_c2patool_is_reported_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(image_meta, "which", lambda cmd: None)
    path = tmp_path / "any.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")

    tools = image_meta.run_optional_tools(path)

    assert tools["c2patool"] == {"available": False}


# --- A probe that never ran must not read as "no C2PA" -----------------------
#
# c2patool exits non-zero both when an asset carries no manifest and when the
# binary itself fails, so the exit code alone cannot separate the two. The
# interpretation therefore has to key off whether c2patool actually answered
# the question. Real-world trigger: the published Docker image pins a
# multi-arch base digest, so on an arm64 host it resolves to linux/arm64 while
# the Dockerfile still installs the x86_64-unknown-linux-gnu c2patool release
# (upstream ships no linux-aarch64 build). The binary then dies before main().

ROSETTA_CRASH = "rosetta error: failed to open elf at /lib64/ld-linux-x86-64.so.2\n"


def test_crashed_c2patool_is_not_a_clean_verdict(monkeypatch, tmp_path):
    """A binary that dies before main() must be inconclusive, not negative."""
    _fake_c2patool(monkeypatch, ROSETTA_CRASH, -5, on_stderr=True)
    path = tmp_path / "maybe.jpg"
    path.write_bytes(b"\xff\xd8\xff")

    entry = image_meta.run_optional_tools(path)["c2patool"]

    assert entry["has_manifest"] is False, "a crash must never claim a manifest"
    assert entry["ok"] is False, "a crash must be flagged as an unusable verdict"


def test_unrecognised_error_is_inconclusive(monkeypatch, tmp_path):
    """Any error that is not a recognised 'no manifest' marker is inconclusive."""
    _fake_c2patool(monkeypatch, "Error: permission denied\n", 1, on_stderr=True)
    path = tmp_path / "maybe.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")

    entry = image_meta.run_optional_tools(path)["c2patool"]

    assert entry["has_manifest"] is False
    assert entry["ok"] is False


def test_conclusive_runs_are_marked_ok(monkeypatch, tmp_path):
    """Both real verdicts -- manifest and no-manifest -- stay conclusive."""
    path = tmp_path / "a.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")

    _fake_c2patool(monkeypatch, "Error: No claim found\n", 1, on_stderr=True)
    absent = image_meta.run_optional_tools(path)["c2patool"]
    assert absent["has_manifest"] is False
    assert absent["ok"] is True

    _fake_c2patool(monkeypatch, MANIFEST_OUTPUT, 0)
    present = image_meta.run_optional_tools(path)["c2patool"]
    assert present["has_manifest"] is True
    assert present["ok"] is True


def test_timeout_is_inconclusive(monkeypatch, tmp_path):
    """An exception path must still report the probe as unusable."""

    def fake_which(cmd: str):
        return "/fake/bin/c2patool" if cmd == "c2patool" else None

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(image_meta, "which", fake_which)
    monkeypatch.setattr(image_meta.subprocess, "run", fake_run)
    path = tmp_path / "slow.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")

    entry = image_meta.run_optional_tools(path)["c2patool"]

    assert entry["ok"] is False
    assert entry["has_manifest"] is False


def test_inconclusive_probe_is_surfaced_in_the_report(monkeypatch, tmp_path):
    """The failure has to reach the caller, not just the tools dict.

    `has_c2pa: False` next to a silently dead probe is the actual hazard: it
    reads as "this asset carries no C2PA" on the one check a user would trust.
    """
    _fake_c2patool(monkeypatch, ROSETTA_CRASH, -5, on_stderr=True)
    path = tmp_path / "shot.jpg"
    path.write_bytes(b"\xff\xd8\xff")

    report = image_meta.inspect_image(path)

    assert report.has_c2pa is False
    joined = " ".join(report.notes).lower()
    assert "c2patool" in joined and "inconclusive" in joined, (
        f"inconclusive C2PA probe left no trace in the report: {report.notes}"
    )


# --- /capabilities must not advertise a binary that cannot run ---------------


def test_capabilities_rejects_a_present_but_unrunnable_tool(monkeypatch):
    """Presence on PATH is not the same as being usable.

    An x86_64 c2patool inside an arm64 image satisfies `which` and still dies
    before main(); advertising it as available is what makes the downstream
    probe failure look like a considered "no C2PA" answer.
    """
    import server

    server._tool_usable.cache_clear()
    monkeypatch.setattr(server, "which", lambda cmd: f"/usr/local/bin/{cmd}")

    def fake_run(cmd, **kwargs):
        if cmd[0].endswith("c2patool"):
            return _Completed(-5, stderr=ROSETTA_CRASH)
        return _Completed(0, stdout="1.0\n")

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    tools = server.capabilities()["tools"]

    assert tools["c2patool"] is False, "a binary that cannot execute is not available"
    assert tools["exiftool"] is True
    assert tools["qpdf"] is True
    server._tool_usable.cache_clear()
