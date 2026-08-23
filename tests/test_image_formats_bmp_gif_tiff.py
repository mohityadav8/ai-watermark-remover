"""Tests for BMP, GIF, and TIFF (classic + BigTIFF) metadata handling."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "service" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from format_dispatch import classify_bytes
from image_meta import (
    clean_image,
    detect_format,
    inspect_bmp,
    inspect_gif,
    inspect_image,
    inspect_tiff,
    strip_bmp,
    strip_gif,
    strip_tiff,
)


def _minimal_bmp(*, trailing: bytes = b"") -> bytes:
    pixel = b"\x00\x00\xff\xff"  # BGRA, 4 bytes (1x1 32-bit, no row padding)
    dib = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 32, 0, len(pixel), 0, 0, 0, 0)
    data_offset = 14 + len(dib)
    header = b"BM" + struct.pack("<IHHI", data_offset + len(pixel), 0, 0, data_offset)
    return header + dib + pixel + trailing


def _gif_sub_block(size: int, payload: bytes) -> bytes:
    return bytes([size]) + payload


def _gif_extension(label: int, payload: bytes) -> bytes:
    out = b"\x21" + bytes([label])
    pos = 0
    while pos < len(payload):
        chunk = payload[pos : pos + 255]
        out += _gif_sub_block(len(chunk), chunk)
        pos += 255
    out += b"\x00"
    return out


def _minimal_gif(*extensions: tuple[int, bytes], image: bytes = b"") -> bytes:
    lsd = struct.pack("<HHBBB", 1, 1, 0x00, 0x00, 0x00)
    if not image:
        image = (
            b"\x2c"
            + struct.pack("<HHHHB", 0, 0, 1, 1, 0x00)
            + b"\x02"
            + _gif_sub_block(2, b"\x02\x44")
            + b"\x00"
        )
    return (
        b"GIF89a"
        + lsd
        + b"".join(_gif_extension(lbl, p) for lbl, p in extensions)
        + image
        + b"\x3b"
    )


GIF_XMP = (
    b"XMP DataXMP"
    + b'<x:xmpmeta><rdf:RDF><rdf:Description digitalSourceType="trainedAlgorithmicMedia"/></rdf:RDF></x:xmpmeta>'
)


def _entry(bo: str, big: bool, tag: int, ftype: int, fcount: int, value: bytes) -> bytes:
    count_fmt = bo + ("Q" if big else "I")
    return struct.pack(bo + "HH", tag, ftype) + struct.pack(count_fmt, fcount) + value


def _minimal_tiff(*, big_endian: bool = False, big: bool = False, with_meta: bool = True):
    bo = "<" if not big_endian else ">"
    off_fmt = bo + ("Q" if big else "I")
    count_fmt = bo + ("Q" if big else "H")
    count_len = 8 if big else 2
    off_len = 8 if big else 4
    entry_size = 20 if big else 12
    header_size = 16 if big else 8
    xmp = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description '
        b'digitalSourceType="trainedAlgorithmicMedia"/></rdf:RDF></x:xmpmeta>'
    )
    make = b"Acme Corp\x00"
    dt = b"2024:01:01 10:00:00\x00"
    maker = b"AIGC maker\x00"
    strip_data = b"\xaa\xbb\xcc\xdd\xee\xff\x00\x11"

    def short_val(v: int) -> bytes:
        return struct.pack(bo + "H", v) + b"\x00" * (off_len - 2)

    def long_val(v: int) -> bytes:
        return struct.pack(off_fmt, v)

    ifd0 = [
        (256, 4, 1, long_val(1)),
        (257, 4, 1, long_val(1)),
        (258, 3, 1, short_val(8)),
        (259, 3, 1, short_val(1)),
        (262, 3, 1, short_val(2)),
        (273, 4, 1, b"\x00" * off_len),
        (278, 4, 1, long_val(1)),
        (279, 4, 1, long_val(len(strip_data))),
    ]
    if with_meta:
        ifd0 += [
            (271, 2, len(make), b"\x00" * off_len),
            (700, 2, len(xmp), b"\x00" * off_len),
            (34665, 4, 1, b"\x00" * off_len),
        ]
    count = len(ifd0)
    ifd0_off = header_size
    ifd0_end = ifd0_off + count_len + count * entry_size + off_len
    cursor = ifd0_end

    exif_count = 3 if with_meta else 0
    exif_off = cursor if with_meta else 0
    if with_meta:
        cursor += count_len + exif_count * entry_size + off_len
        exp_payload = struct.pack(bo + "II", 1, 100)
        exp_off = cursor
        cursor += len(exp_payload)
        dt_off = cursor
        cursor += len(dt)
        mn_off = cursor
        cursor += len(maker)

    strip_off = cursor
    cursor += len(strip_data)
    make_off = cursor
    cursor += len(make)
    xmp_off = cursor
    cursor += len(xmp)

    ifd0_bytes = bytearray(struct.pack(count_fmt, count))
    for tag, ftype, fcount, _v in ifd0:
        if tag == 273:
            ifd0_bytes += _entry(bo, big, tag, 4, 1, long_val(strip_off))
        elif tag == 271:
            ifd0_bytes += _entry(bo, big, tag, 2, len(make), long_val(make_off))
        elif tag == 700:
            ifd0_bytes += _entry(bo, big, tag, 2, len(xmp), long_val(xmp_off))
        elif tag == 34665:
            ifd0_bytes += _entry(bo, big, tag, 4, 1, long_val(exif_off))
        else:
            ifd0_bytes += _entry(bo, big, tag, ftype, fcount, _v)
    ifd0_bytes += struct.pack(off_fmt, 0)

    if with_meta:
        exif_bytes = bytearray(struct.pack(count_fmt, exif_count))
        exif_bytes += _entry(bo, big, 33434, 5, 1, long_val(exp_off))
        exif_bytes += _entry(bo, big, 36867, 2, len(dt), long_val(dt_off))
        exif_bytes += _entry(bo, big, 37500, 2, len(maker), long_val(mn_off))
        exif_bytes += struct.pack(off_fmt, 0)
        tail = exp_payload + dt + maker + strip_data + make + xmp
    else:
        exif_bytes = b""
        tail = strip_data + make + xmp

    if big:
        header = (
            (b"II" if not big_endian else b"MM")
            + struct.pack(bo + "H", 43)
            + struct.pack(bo + "H", 8)
            + struct.pack(bo + "H", 0)
            + struct.pack(off_fmt, ifd0_off)
        )
    else:
        header = (
            (b"II" if not big_endian else b"MM")
            + struct.pack(bo + "H", 42)
            + struct.pack(off_fmt, ifd0_off)
        )
    return header + bytes(ifd0_bytes) + bytes(exif_bytes) + tail, strip_off, strip_data


def test_detect_format_bmp_gif_tiff():
    assert detect_format(_minimal_bmp()) == "bmp"
    assert detect_format(_minimal_gif()) == "gif"
    le, _o, _d = _minimal_tiff()
    be, _o2, _d2 = _minimal_tiff(big_endian=True)
    bigle, _o3, _d3 = _minimal_tiff(big=True)
    bigbe, _o4, _d4 = _minimal_tiff(big=True, big_endian=True)
    assert detect_format(le) == "tiff"
    assert detect_format(be) == "tiff"
    assert detect_format(bigle) == "tiff"
    assert detect_format(bigbe) == "tiff"
    assert detect_format(b"GIF87a rest") == "gif"


def test_format_dispatch_bmp_gif_tiff():
    assert classify_bytes(_minimal_bmp(), ".bmp") == "image"
    assert classify_bytes(_minimal_gif(), ".gif") == "image"
    tiff_le, _o, _d = _minimal_tiff()
    assert classify_bytes(tiff_le, ".tiff") == "image"
    assert classify_bytes(tiff_le, ".tif") == "image"
    assert classify_bytes(tiff_le, "") == "image"
    assert classify_bytes(_minimal_gif(), "") == "image"
    bigle, _o2, _d2 = _minimal_tiff(big=True)
    assert classify_bytes(bigle, "") == "image"


def test_bmp_clean_has_no_flags():
    data = _minimal_bmp()
    has_c2pa, has_ai, findings = inspect_bmp(data)
    assert has_c2pa is False
    assert has_ai is False
    assert any("no metadata" in f.lower() for f in findings)
    cleaned, actions = strip_bmp(data)
    assert cleaned == data
    assert any("no BMP trailing" in a for a in actions)


def test_bmp_trailing_metadata_detected_and_stripped():
    xmp = b'<x:xmpmeta><rdf:Description digitalSourceType="trainedAlgorithmicMedia"/></x:xmpmeta>'
    data = _minimal_bmp(trailing=xmp)
    _has_c2pa, has_ai, findings = inspect_bmp(data)
    assert has_ai is True
    assert any("trailing" in f.lower() for f in findings)

    cleaned, actions = strip_bmp(data)
    assert any("drop" in a for a in actions)
    assert xmp not in cleaned
    assert cleaned == _minimal_bmp()
    assert struct.unpack("<I", cleaned[2:6])[0] == len(cleaned)


def test_bmp_keep_non_ai_metadata():
    xmp = b"trailing bytes without markers"
    data = _minimal_bmp(trailing=xmp)
    kept, actions = strip_bmp(data, strip_all_metadata=False)
    assert kept == data
    assert any("keep" in a.lower() for a in actions)


def test_bmp_roundtrip(tmp_path: Path):
    src = tmp_path / "pixel.bmp"
    src.write_bytes(_minimal_bmp(trailing=b"<x:xmpmeta>OpenAI</x:xmpmeta>"))
    dest = tmp_path / "pixel.cleaned.bmp"
    report = clean_image(src, dest)
    assert report["format"] == "bmp"
    assert report["still_has_ai_metadata"] is False
    assert dest.read_bytes() == _minimal_bmp()


def test_gif_inspect_detects_comment_and_xmp():
    data = _minimal_gif((0xFE, b"made with c2pa tools"), (0xFF, GIF_XMP))
    has_c2pa, has_ai, findings = inspect_gif(data)
    assert has_ai is True
    assert has_c2pa is True
    assert any("comment" in f.lower() for f in findings)
    assert any("xmp" in f.lower() for f in findings)


def test_gif_strip_removes_metadata_keeps_pixels_and_loop(tmp_path: Path):
    image = (
        b"\x2c"
        + struct.pack("<HHHHB", 0, 0, 1, 1, 0x00)
        + b"\x02"
        + _gif_sub_block(2, b"\x02\x44")
        + b"\x00"
    )
    loop = b"NETSCAPE2.0" + struct.pack("<H", 0)
    data = _minimal_gif(
        (0xFE, b"made with c2pa tools"),
        (0xFF, GIF_XMP),
        (0xFF, loop),
        (0xF9, b"\x00\x00\x00\x00"),
        image=image,
    )
    cleaned, actions = strip_gif(data)
    assert any("drop GIF comment" in a for a in actions)
    assert any("drop GIF XMP application" in a for a in actions)
    assert b"c2pa tools" not in cleaned
    assert b"XMP DataXMP" not in cleaned
    assert loop in cleaned
    assert b"\x21\xf9\x04" in cleaned
    assert image in cleaned
    has_c2pa, has_ai, _findings = inspect_gif(cleaned)
    assert has_c2pa is False
    assert has_ai is False


def test_gif_keep_non_ai_metadata():
    plain = _minimal_gif((0xFE, b"just a comment"))
    _stripped, actions = strip_gif(plain)
    assert any("drop GIF comment" in a for a in actions)
    kept, actions2 = strip_gif(plain, strip_all_metadata=False)
    assert b"just a comment" in kept
    assert not any("drop GIF" in a for a in actions2)


def test_gif_global_color_table_preserved():
    header = b"GIF89a"
    lsd = struct.pack("<HHBBB", 2, 2, 0x91, 0, 0)
    ncolors = 1 << ((0x91 & 0x07) + 1)
    gct = bytes(range(3 * ncolors))
    image = (
        b"\x2c"
        + struct.pack("<HHHHB", 0, 0, 2, 2, 0x00)
        + b"\x02"
        + _gif_sub_block(2, b"\x02\x44")
        + b"\x00"
    )
    data = header + lsd + gct + _gif_extension(0xFE, b"hello") + image + b"\x3b"
    cleaned, actions = strip_gif(data)
    assert any("drop GIF comment" in a for a in actions)
    assert gct in cleaned
    assert image in cleaned
    assert b"hello" not in cleaned
    assert detect_format(cleaned) == "gif"


def test_gif_roundtrip(tmp_path: Path):
    src = tmp_path / "anim.gif"
    src.write_bytes(_minimal_gif((0xFE, b"made with c2pa tools"), (0xFF, GIF_XMP)))
    dest = tmp_path / "anim.cleaned.gif"
    report = clean_image(src, dest)
    assert report["format"] == "gif"
    assert report["still_has_c2pa"] is False
    assert report["still_has_ai_metadata"] is False
    rep = inspect_image(dest)
    assert rep.format == "gif"
    assert rep.has_ai_metadata is False


def test_tiff_inspect_detects_metadata():
    data, _off, _strip = _minimal_tiff()
    _has_c2pa, has_ai, findings = inspect_tiff(data)
    assert has_ai is True
    assert any("XMP" in f for f in findings)
    assert any("ExifIFD" in f for f in findings)
    assert any("MakerNote" in f for f in findings)
    assert any("trainedAlgorithmicMedia" in f for f in findings)


def test_tiff_strip_removes_metadata_preserves_strip():
    data, strip_off, strip_data = _minimal_tiff()
    cleaned, actions = strip_tiff(data)
    assert any("drop TIFF tag 700" in a for a in actions)
    assert any("drop TIFF tag 34665" in a for a in actions)
    assert any("drop TIFF tag 271" in a for a in actions)
    assert b"Acme Corp" not in cleaned
    assert b"trainedAlgorithmicMedia" not in cleaned
    assert b"AIGC" not in cleaned
    assert b"2024:01:01" not in cleaned
    assert cleaned[strip_off : strip_off + len(strip_data)] == strip_data
    assert detect_format(cleaned) == "tiff"
    has_c2pa, has_ai, _findings = inspect_tiff(cleaned)
    assert has_c2pa is False
    assert has_ai is False


def test_tiff_bigtiff_strip():
    for kwargs in ({"big": True}, {"big": True, "big_endian": True}):
        data, strip_off, strip_data = _minimal_tiff(**kwargs)
        cleaned, actions = strip_tiff(data)
        assert any("drop TIFF tag 700" in a for a in actions)
        assert b"Acme Corp" not in cleaned
        assert b"trainedAlgorithmicMedia" not in cleaned
        assert cleaned[strip_off : strip_off + len(strip_data)] == strip_data
        assert detect_format(cleaned) == "tiff"
        has_c2pa, has_ai, _findings = inspect_tiff(cleaned)
        assert has_c2pa is False
        assert has_ai is False


def test_tiff_clean_roundtrip_byte_identical():
    data, _off, _strip = _minimal_tiff(with_meta=False)
    cleaned, actions = strip_tiff(data)
    assert cleaned == data
    assert any("no TIFF metadata tags removed" in a for a in actions)


def test_tiff_keep_non_ai_metadata():
    data, _off, _strip = _minimal_tiff()
    cleaned, actions = strip_tiff(data, strip_all_metadata=False)
    assert any("drop TIFF tag 700" in a for a in actions)
    assert any("drop TIFF tag 34665" in a for a in actions)
    assert not any("drop TIFF tag 271" in a for a in actions)
    assert b"Acme Corp" in cleaned
    assert b"AIGC" not in cleaned


def test_tiff_roundtrip(tmp_path: Path):
    data, _off, _strip = _minimal_tiff()
    src = tmp_path / "photo.tiff"
    src.write_bytes(data)
    dest = tmp_path / "photo.cleaned.tiff"
    report = clean_image(src, dest)
    assert report["format"] == "tiff"
    assert report["still_has_c2pa"] is False
    assert report["still_has_ai_metadata"] is False
    rep = inspect_image(dest)
    assert rep.format == "tiff"
    assert rep.has_ai_metadata is False
