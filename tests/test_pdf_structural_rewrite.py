"""Tests for the qpdf structural rewrite that follows the exiftool PDF pass.

exiftool writes PDFs *incrementally*: `-all=` appends a %BeginExifToolUpdate
block that frees the Info object and drops /Info from the trailer, but the
original metadata bytes stay in the file and exiftool itself can revert them
with -PDF-update:all=. For a provenance-stripping tool that is a silent leak,
so clean_pdf re-serializes the document with qpdf to drop the now-unreferenced
objects.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "service" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import container_meta
from container_meta import clean_pdf


class _Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _ai_pdf() -> bytes:
    """A small but structurally valid PDF carrying AI provenance in /Info."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << >> >>",
        b"<< /Producer (Claude Opus) /Creator (Anthropic Claude) >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info 4 0 R >>\n" % (len(objs) + 1)
    out += b"startxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


def _fake_tools(monkeypatch, *, qpdf: bool, qpdf_rc: int = 0, qpdf_writes: bool = True):
    """Pretend exiftool exists (a no-op) and qpdf exists or not."""
    seen: list[list[str]] = []

    def fake_which(cmd: str):
        if cmd == "exiftool":
            return "/fake/bin/exiftool"
        if cmd == "qpdf":
            return "/fake/bin/qpdf" if qpdf else None
        return None

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        if cmd[0].endswith("qpdf"):
            if qpdf_writes:
                # qpdf writes its rebuilt document to the final argument.
                Path(cmd[-1]).write_bytes(b"%PDF-1.4\n% rebuilt\n%%EOF\n")
            return _Completed(qpdf_rc)
        return _Completed(0)

    monkeypatch.setattr(container_meta, "which", fake_which)
    monkeypatch.setattr(container_meta.subprocess, "run", fake_run)
    return seen


def test_without_qpdf_the_incremental_leak_is_reported(monkeypatch, tmp_path: Path):
    """No qpdf: the clean must not claim success it cannot deliver.

    This is the regression guard. Before the structural rewrite existed,
    clean_pdf returned only "exiftool -all= (rc=0)" and nothing told the caller
    that the metadata bytes were still sitting in the file.
    """
    src = tmp_path / "in.pdf"
    dest = tmp_path / "out.pdf"
    src.write_bytes(_ai_pdf())
    _fake_tools(monkeypatch, qpdf=False)

    actions, meta = clean_pdf(src, dest)

    assert meta["structural_rewrite"] is False
    assert any("incremental" in a for a in actions), actions
    assert any("qpdf" in a for a in actions), actions


def test_with_qpdf_the_document_is_rebuilt(monkeypatch, tmp_path: Path):
    src = tmp_path / "in.pdf"
    dest = tmp_path / "out.pdf"
    src.write_bytes(_ai_pdf())
    seen = _fake_tools(monkeypatch, qpdf=True)

    _actions, meta = clean_pdf(src, dest)

    assert meta["structural_rewrite"] is True
    qpdf_cmd = [c for c in seen if c[0].endswith("qpdf")]
    assert len(qpdf_cmd) == 1
    assert "--linearize" in qpdf_cmd[0]
    # The rebuilt file must replace the exiftool output, not sit beside it.
    assert dest.read_bytes() == b"%PDF-1.4\n% rebuilt\n%%EOF\n"
    assert not list(tmp_path.glob("*.qpdf-tmp"))


def test_qpdf_warning_exit_code_still_counts(monkeypatch, tmp_path: Path):
    """qpdf returns 3 for 'succeeded with warnings' and still writes output."""
    src = tmp_path / "in.pdf"
    dest = tmp_path / "out.pdf"
    src.write_bytes(_ai_pdf())
    _fake_tools(monkeypatch, qpdf=True, qpdf_rc=3)

    actions, meta = clean_pdf(src, dest)

    assert meta["structural_rewrite"] is True
    assert any("rc=3" in a for a in actions), actions


def test_qpdf_failure_keeps_exiftool_output_and_warns(monkeypatch, tmp_path: Path):
    src = tmp_path / "in.pdf"
    dest = tmp_path / "out.pdf"
    src.write_bytes(_ai_pdf())
    _fake_tools(monkeypatch, qpdf=True, qpdf_rc=2, qpdf_writes=False)

    actions, meta = clean_pdf(src, dest)

    assert meta["structural_rewrite"] is False
    assert dest.is_file()
    assert any("recoverable" in a for a in actions), actions
    assert not list(tmp_path.glob("*.qpdf-tmp"))


@pytest.mark.skipif(
    shutil.which("exiftool") is None or shutil.which("qpdf") is None,
    reason="needs real exiftool and qpdf",
)
def test_end_to_end_no_recoverable_metadata_bytes(tmp_path: Path):
    """The whole point: nothing readable survives in the output bytes.

    Run against the real tools this fails without the structural rewrite —
    exiftool alone leaves '/Producer (Claude Opus)' verbatim in the file.
    """
    src = tmp_path / "in.pdf"
    dest = tmp_path / "out.pdf"
    src.write_bytes(_ai_pdf())

    _actions, meta = clean_pdf(src, dest)

    # Assert the leak itself before the bookkeeping flag, so reverting the
    # rewrite fails here — on recoverable bytes — and not on a missing key.
    body = dest.read_bytes()
    assert b"Claude" not in body
    assert b"Anthropic" not in body
    assert meta["structural_rewrite"] is True
