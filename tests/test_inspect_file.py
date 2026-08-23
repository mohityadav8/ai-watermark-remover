"""CLI inspect_file must name the file being inspected (issue #31)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "service" / "scripts"
INSPECT = SCRIPTS / "inspect_file.py"
FIXTURE = ROOT / "tests" / "fixtures" / "sample_watermarked.txt"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(INSPECT), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_human_report_starts_with_file_line():
    result = _run(str(FIXTURE))
    first = result.stdout.splitlines()[0]
    assert first == f"File: {FIXTURE.resolve()}"
    assert "Kind: text" in result.stdout


def test_json_report_includes_path():
    result = _run("--json", str(FIXTURE))
    payload = json.loads(result.stdout)
    assert payload["path"] == str(FIXTURE.resolve())
    assert payload["kind"] == "text"
