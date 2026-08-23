"""Truncated ISOBMFF containers still run the whole-file C2PA byte scan (#167).

A first box whose size overruns the file (a truncated AVIF/HEIC/MP4 download)
used to return early with a not-a-valid finding, skipping the byte-scan
fallback every sibling inspector reaches — literal b"c2pa" could be present
and the report still said clean.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "service" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import image_meta


def _avif(marker: bytes, first_box_overruns: bool) -> bytes:
    ftyp = b"\x00\x00\x00\x14ftypavif" + b"\x00\x00\x00\x00"  # 20-byte ftyp
    body = marker + b"\x00" * 16
    if first_box_overruns:
        # First box (ftyp) declares a size larger than the whole file.
        size = len(ftyp) + len(body) + 64
        head = struct.pack(">I", size) + b"ftypavif" + b"\x00" * 8
        return head + body
    return struct.pack(">I", len(ftyp)) + b"ftypavif" + b"\x00" * 8 + body


def test_truncated_avif_still_bytescans_for_c2pa():
    data = _avif(b"c2pa contentcredentials", first_box_overruns=True)
    assert b"c2pa" in data

    has_c2pa, _, findings = image_meta.inspect_isobmff(data, fmt="avif")
    # The byte-scan fallback fires instead of the early clean-looking return.
    assert has_c2pa is True, findings
    assert any("byte-scan C2PA markers" in f for f in findings), findings
    # The parse failure is still reported.
    assert any("no ISOBMFF boxes found" in f for f in findings), findings


def test_intact_avif_box_parse_path_unchanged():
    data = _avif(b"c2pa contentcredentials", first_box_overruns=False)
    has_c2pa, _, findings = image_meta.inspect_isobmff(data, fmt="avif")
    assert has_c2pa is True
    # A walkable container finds the C2PA box directly (stronger evidence than
    # the byte scan); the parse-failure note must not appear.
    assert any("top-level box" in f and "c2pa" in f.lower() for f in findings), findings
    assert not any("no ISOBMFF boxes found" in f for f in findings)


def test_truncated_markerless_avif_reports_parse_failure():
    data = _avif(b"nothing interesting", first_box_overruns=True)
    has_c2pa, _, findings = image_meta.inspect_isobmff(data, fmt="avif")
    assert has_c2pa is False
    assert findings == ["not a valid AVIF (no ISOBMFF boxes found)"]
