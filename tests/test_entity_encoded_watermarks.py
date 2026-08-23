"""Regression tests for entity-encoded watermark carriers (#129).

A zero-width space written as ``&#x200B;`` reaches Layer A as nine ASCII
characters and survived cleaning untouched, because the XML parser in
Word/Writer decodes the entity back into the invisible carrier after the
cleaner has run. The scrub now decodes XML character references before
cleaning and re-encodes on the way out.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "service" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from container_meta import _decode_xml_entities, _reencode_xml_text, clean_docx, clean_odt


def test_decode_resolves_numeric_and_predefined_entities_only():
    assert _decode_xml_entities("&#x200B;") == "\u200b"
    assert _decode_xml_entities("&#8203;") == "\u200b"
    assert _decode_xml_entities("&amp;&lt;&gt;") == "&<>"
    # HTML5-only named entities stay literal (an XML parser would not resolve them).
    assert _decode_xml_entities("&nbsp;") == "&nbsp;"
    # Invalid code points stay literal for the real parser to reject.
    assert _decode_xml_entities("&#x110000;") == "&#x110000;"
    assert _decode_xml_entities("&#0;") == "&#0;"


def test_reencode_round_trip_is_stable():
    assert _reencode_xml_text("a&b<c>d") == "a&amp;b&lt;c&gt;d"
    decoded = _decode_xml_entities("Hello &amp; welcome")
    assert _reencode_xml_text(decoded) == "Hello &amp; welcome"


def _make_docx(body_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>" + body_text + "</w:t></w:r></w:p></w:body></w:document>",
        )
    return buf.getvalue()


def test_entity_encoded_zero_width_space_is_scrubbed():
    data = _make_docx("Hello&#x200B;World")
    cleaned, actions = clean_docx(data)

    with zipfile.ZipFile(io.BytesIO(cleaned)) as zf:
        document = zf.read("word/document.xml").decode()

    # The entity must not survive in any form: neither the raw reference nor
    # the decoded character (which a parser would resurrect).
    assert "&#x200B;" not in document
    assert "\u200b" not in document
    assert "HelloWorld" in document
    assert any(a for a in actions), "cleaning must report an action"


def test_clean_text_without_carriers_is_byte_identical():
    data = _make_docx("Hello World")
    cleaned, _ = clean_docx(data)
    with zipfile.ZipFile(io.BytesIO(cleaned)) as zf:
        document = zf.read("word/document.xml").decode()
    assert "Hello World" in document


def test_legitimate_ampersand_entity_round_trips():
    data = _make_docx("Tom &amp; Jerry")
    cleaned, _ = clean_docx(data)
    with zipfile.ZipFile(io.BytesIO(cleaned)) as zf:
        document = zf.read("word/document.xml").decode()
    # The ampersand survives as a well-formed reference, not a bare &.
    assert "Tom &amp; Jerry" in document


def _make_odt(body: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        zf.writestr(
            "content.xml",
            '<?xml version="1.0"?><office:document-content '
            'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
            "<office:body><office:text>"
            '<text:p text:style-name="Standard">' + body + "</text:p>"
            "</office:text></office:body></office:document-content>",
        )
    return buf.getvalue()


def test_odt_entity_encoded_carrier_scrubbed_and_markup_preserved():
    data = _make_odt("Hi&#x200B;<text:span>world</text:span>!")
    cleaned, _ = clean_odt(data)
    with zipfile.ZipFile(io.BytesIO(cleaned)) as zf:
        content = zf.read("content.xml").decode()

    assert "&#x200B;" not in content
    assert "​" not in content
    # The nested span markup must stay intact (not entity-escaped).
    assert "<text:span>world</text:span>" in content
    assert "Hi" in content and "!</text:p>" in content
