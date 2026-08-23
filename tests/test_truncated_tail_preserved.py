"""Truncated-tail preservation in strip_png / strip_isobmff (#170).

An unparseable chunk/box used to end the walk with the remainder dropped from
the output — a recoverable image became an unopenable husk while the run
reported "already clean". The tail must be kept and the run must say so.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "service" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import image_meta


def _chunk(ctype: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + ctype
        + payload
        + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF)
    )


def _truncated_png() -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    idat_payload = b"\x78\x9c\x00" + b"\x00" * 40
    head = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
    # An IDAT whose declared length overruns the file (interrupted download):
    # 8-byte header + declared payload + 4-byte CRC > remaining bytes.
    truncated_idat = struct.pack(">I", len(idat_payload)) + b"IDAT" + idat_payload[:8]
    return head + truncated_idat  # no CRC, no IEND


def test_strip_png_keeps_truncated_tail():
    data = _truncated_png()
    out, actions = image_meta.strip_png(data)
    # No chunk in this fixture is droppable, so every input byte must survive
    # byte-for-byte: nothing is silently dropped.
    assert out == data, (len(out), len(data))
    # And the run says the tail was kept — never "already clean".
    assert any("truncated" in a.lower() for a in actions), actions


def test_strip_png_intact_unchanged():
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    data = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IEND", b"")
    out, actions = image_meta.strip_png(data)
    assert out == data
    assert not any("truncated" in a.lower() for a in actions)


def test_strip_isobmff_keeps_truncated_tail():
    # ftyp + a truncated mdat (declared size overruns the file).
    ftyp = struct.pack(">I", 16) + b"ftypavif" + b"\x00\x00\x00\x00"
    mdat_declared = 200
    mdat = struct.pack(">I", mdat_declared) + b"mdat" + b"\x00" * 40
    data = ftyp + mdat

    out, actions = image_meta.strip_isobmff(data, fmt="avif")
    # The truncated mdat tail is preserved, not dropped.
    assert len(out) >= len(data), (len(out), len(data))
    assert any("truncated" in a.lower() for a in actions), actions


def test_strip_isobmff_intact_unchanged():
    ftyp = struct.pack(">I", 16) + b"ftypavif" + b"\x00\x00\x00\x00"
    full = struct.pack(">I", 12) + b"mdat" + b"\x00" * 4
    data = ftyp + full
    _, actions = image_meta.strip_isobmff(data, fmt="avif")
    assert not any("truncated" in a.lower() for a in actions)


def test_strip_isobmff_trailing_junk_kept_without_truncation():
    # A few trailing bytes after the last box are kept verbatim but are not
    # reported as truncation -- the walk simply ran out of box headers.
    ftyp = struct.pack(">I", 16) + b"ftypavif" + b"\x00\x00\x00\x00"
    data = ftyp + b"\x01\x02\x03"
    out, actions = image_meta.strip_isobmff(data, fmt="avif")
    assert out == data
    assert not any("truncated" in a.lower() for a in actions)
