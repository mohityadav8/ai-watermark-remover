"""Tests for AVIF and HEIC (ISOBMFF) metadata and C2PA stripping."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "service" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from format_dispatch import classify_bytes
from image_meta import (
    XMP_UUID,
    clean_image,
    detect_format,
    inspect_image,
    inspect_isobmff,
    strip_isobmff,
)


def _build_box(fourcc: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + fourcc + payload


def _build_full_box(fourcc: bytes, version: int, flags: int, payload: bytes) -> bytes:
    vf = struct.pack(">I", (version << 24) | (flags & 0xFFFFFF))
    return _build_box(fourcc, vf + payload)


def _minimal_avif_with_c2pa_and_xmp() -> bytes:
    """Minimal ISOBMFF AVIF with ftyp, meta (with XMP uuid + jumb sub-box), top-level jumb, and mdat."""
    ftyp = _build_box(b"ftyp", b"avif\x00\x00\x00\x00avifmif1")
    jumb_sub = _build_box(b"jumb", b"c2pa.manifest.store.v1")
    xmp_uuid_sub = _build_box(
        b"uuid",
        XMP_UUID
        + b"<?xpacket begin='' id='W5M0MpCehiHzreSzNTczkc9d'?><x:xmpmeta xmlns:x='adobe:ns:meta/'><rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'><rdf:Description rdf:about='' xmlns:ai='http://ns.adobe.com/ai/'><ai:GeneratedBy>Midjourney</ai:GeneratedBy></rdf:Description></rdf:RDF></x:xmpmeta>",
    )
    hdlr = _build_full_box(
        b"hdlr",
        0,
        0,
        b"\x00\x00\x00\x00pict\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00PictureHandler\x00",
    )
    meta = _build_full_box(b"meta", 0, 0, hdlr + jumb_sub + xmp_uuid_sub)
    top_jumb = _build_box(b"jumb", b"c2pa.claim.v1 contentcredentials")
    mdat = _build_box(b"mdat", b"\x00\x01\x02\x03\x04\x05image_pixel_data")
    return ftyp + meta + top_jumb + mdat


def _minimal_heic_with_xmp() -> bytes:
    """Minimal ISOBMFF HEIC with ftyp, meta, and XMP uuid box."""
    ftyp = _build_box(b"ftyp", b"heic\x00\x00\x00\x00mif1heic")
    hdlr = _build_full_box(
        b"hdlr",
        0,
        0,
        b"\x00\x00\x00\x00pict\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00PictureHandler\x00",
    )
    xmp_uuid = _build_box(
        b"uuid",
        XMP_UUID
        + b"<x:xmpmeta xmlns:x='adobe:ns:meta/'><rdf:RDF><rdf:Description digitalSourceType='trainedAlgorithmicMedia'/></rdf:RDF></x:xmpmeta>",
    )
    meta = _build_full_box(b"meta", 0, 0, hdlr)
    mdat = _build_box(b"mdat", b"\xaa\xbb\xcc\xddhevc_stream")
    return ftyp + meta + xmp_uuid + mdat


def test_detect_format_avif_and_heic():
    avif_bytes = _minimal_avif_with_c2pa_and_xmp()
    assert detect_format(avif_bytes) == "avif"

    heic_bytes = _minimal_heic_with_xmp()
    assert detect_format(heic_bytes) == "heic"

    avis_bytes = _build_box(b"ftyp", b"avis\x00\x00\x00\x00avis") + b"data"
    assert detect_format(avis_bytes) == "avif"

    mif1_bytes = _build_box(b"ftyp", b"mif1\x00\x00\x00\x00mif1heic") + b"data"
    assert detect_format(mif1_bytes) == "heic"


def test_inspect_isobmff_detects_c2pa_and_xmp():
    avif_bytes = _minimal_avif_with_c2pa_and_xmp()
    has_c2pa, has_ai, findings = inspect_isobmff(avif_bytes, "avif")
    assert has_c2pa is True
    assert has_ai is True
    assert any("C2PA" in f or "jumb" in f.lower() for f in findings)
    assert any("uuid" in f.lower() or "XMP" in f for f in findings)


def test_inspect_isobmff_heic_ai_metadata():
    heic_bytes = _minimal_heic_with_xmp()
    _has_c2pa, has_ai, findings = inspect_isobmff(heic_bytes, "heic")
    assert has_ai is True
    assert any("trainedAlgorithmicMedia" in f or "XMP" in f for f in findings)


def test_strip_isobmff_removes_c2pa_and_xmp():
    avif_bytes = _minimal_avif_with_c2pa_and_xmp()
    cleaned, actions = strip_isobmff(avif_bytes, "avif")

    assert any("jumb" in a.lower() for a in actions)
    assert any("xmp" in a.lower() or "uuid" in a.lower() for a in actions)

    # Re-inspect cleaned bytes
    has_c2pa, has_ai, _findings = inspect_isobmff(cleaned, "avif")
    assert has_c2pa is False
    assert has_ai is False

    # Structure still valid ISOBMFF and starts with ftyp
    assert detect_format(cleaned) == "avif"
    assert b"mdat" in cleaned
    assert b"jumb" not in cleaned.lower()


def test_clean_image_avif_roundtrip(tmp_path: Path):
    src = tmp_path / "photo.avif"
    src.write_bytes(_minimal_avif_with_c2pa_and_xmp())
    dest = tmp_path / "photo.cleaned.avif"

    report = clean_image(src, dest)
    assert dest.is_file()
    assert report["format"] == "avif"
    assert report["still_has_c2pa"] is False
    assert report["still_has_ai_metadata"] is False
    assert any("jumb" in a.lower() for a in report["actions"])

    inspect_rep = inspect_image(dest)
    assert inspect_rep.format == "avif"
    assert inspect_rep.has_c2pa is False
    assert inspect_rep.has_ai_metadata is False


def test_clean_image_heic_roundtrip(tmp_path: Path):
    src = tmp_path / "camera.heic"
    src.write_bytes(_minimal_heic_with_xmp())
    dest = tmp_path / "camera.cleaned.heic"

    report = clean_image(src, dest)
    assert dest.is_file()
    assert report["format"] == "heic"
    assert report["still_has_ai_metadata"] is False
    assert any("uuid" in a.lower() or "xmp" in a.lower() for a in report["actions"])


def test_format_dispatch_avif_heic():
    avif_data = _minimal_avif_with_c2pa_and_xmp()
    heic_data = _minimal_heic_with_xmp()

    assert classify_bytes(avif_data, ".avif") == "image"
    assert classify_bytes(heic_data, ".heic") == "image"
    assert classify_bytes(heic_data, ".heif") == "image"
    assert classify_bytes(avif_data, "") == "image"  # magic bytes sniffing
    assert classify_bytes(heic_data, "") == "image"  # magic bytes sniffing
