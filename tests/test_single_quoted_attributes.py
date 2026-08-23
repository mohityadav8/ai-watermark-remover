"""Regression tests for single-quoted XML attributes in container pruning (#130).

XML permits attribute values in single or double quotes. The relationship and
manifest pruners matched only double quotes, so a valid single-quoted
Relationship/manifest entry parsed as an empty target, resolved to the package
base, and was deleted — corrupting DOCX/ODT packages from tools that emit
single quotes.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "service" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from container_meta import clean_docx


def _make_docx_single_quoted_rels() -> bytes:
    """DOCX whose rels use single quotes for the kept main-document part."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""",
        )
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p></w:body></w:document>',
        )
        zf.writestr(
            "word/_rels/document.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id='rId1' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument' Target='document.xml'/>
  <Relationship Id='rId2' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink' Target='https://example.com/' TargetMode='External'/>
</Relationships>""",
        )
    return buf.getvalue()


def test_single_quoted_relationship_is_not_pruned():
    data = _make_docx_single_quoted_rels()
    cleaned, actions = clean_docx(data)

    with zipfile.ZipFile(io.BytesIO(cleaned)) as zf:
        rels = zf.read("word/_rels/document.xml.rels").decode()

    # The main-document relationship must survive: its target (word/document.xml)
    # still exists, it was only written with single quotes (#130).
    assert "rId1" in rels
    assert "Target='document.xml'" in rels
    # The external hyperlink relationship survives untouched.
    assert "TargetMode='External'" in rels

    assert not any("prune dangling relationships" in a for a in actions)


def test_double_quoted_relationships_still_prune_correctly():
    """The fix must not loosen pruning for dropped parts (double-quoted case)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/customXml/item1.xml" ContentType="application/xml"/>'
            "</Types>",
        )
        zf.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>',
        )
        zf.writestr("customXml/item1.xml", "<root>c2pa contentcredentials</root>")
        zf.writestr(
            "word/_rels/document.xml.rels",
            """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml" Target="../customXml/item1.xml"/>
</Relationships>""",
        )
    data = buf.getvalue()

    cleaned, actions = clean_docx(data)
    with zipfile.ZipFile(io.BytesIO(cleaned)) as zf:
        rels = zf.read("word/_rels/document.xml.rels").decode()

    assert 'Target="document.xml"' in rels
    assert "../customXml/item1.xml" not in rels
    assert any("prune dangling relationships" in a for a in actions)
