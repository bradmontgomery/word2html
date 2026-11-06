"""Conversions that should work, checked against real pandoc."""

from __future__ import annotations

import base64
import re
import zipfile
from pathlib import Path

import pytest
from conftest import (
    CONTENT_TYPES,
    NS,
    PKG_REL,
    PNG,
    REL,
    XML,
    footnote_reference,
    image,
    para,
    relationship,
    run,
)

from word2html import ConversionResult, FindingKind, convert_to_html


def body_of(html: str) -> str:
    return html[html.index("<body") :]


def test_declares_utf8_and_preserves_non_ascii(make_docx):
    path = make_docx(body=para(run("Café — 日本語 “double” it’s")))
    convert_to_html(path)
    raw = path.with_suffix(".html").read_bytes()
    assert b'charset="utf-8"' in raw.lower()
    assert "Café — 日本語 “double” it’s".encode() in raw


def test_is_html5_with_a_title_from_the_filename(make_docx):
    path = make_docx("My Report.docx", body=para(run("x")))
    convert_to_html(path)
    html = path.with_suffix(".html").read_text(encoding="utf-8")
    assert html.lower().startswith("<!doctype html>")
    assert "<title>My Report</title>" in html


def test_keeps_html5_structure_that_tidy_used_to_strip(make_docx):
    path = make_docx(
        body=para(run("HILITE", '<w:highlight w:val="yellow"/>'), footnote_reference()),
        footnotes='<w:footnote w:id="1"><w:p>' + run("FOOTNOTE") + "</w:p></w:footnote>",
    )
    convert_to_html(path)
    html = path.with_suffix(".html").read_text(encoding="utf-8")
    assert "<mark>HILITE</mark>" in html
    assert re.search(r'<section[^>]*id="footnotes"', html)
    assert "FOOTNOTE" in html


def test_text_that_looks_like_html_stays_escaped(make_docx):
    path = make_docx(body=para(run("&lt;script&gt;alert(1)&lt;/script&gt;")))
    result = convert_to_html(path)
    html = result.output_path.read_text(encoding="utf-8")
    assert "<script" not in html
    assert "&lt;script&gt;" in html


def test_tables_survive(make_docx):
    table = (
        '<w:tbl><w:tblGrid><w:gridCol w:w="1000"/></w:tblGrid><w:tr><w:tc><w:p>'
        + run("CELL")
        + "</w:p></w:tc></w:tr></w:tbl>"
    )
    path = make_docx(body=table)
    convert_to_html(path)
    html = path.with_suffix(".html").read_text(encoding="utf-8")
    assert "<table" in html
    assert "CELL" in html


def test_images_are_embedded_not_left_dangling(make_docx):
    path = make_docx(
        body=para(image('r:embed="img"')),
        rels=relationship("img", "image", "media/image1.png"),
        files={"word/media/image1.png": PNG},
    )
    result = convert_to_html(path)
    html = result.output_path.read_text(encoding="utf-8")
    assert "media/image1.png" not in html
    encoded = re.search(r'<img src="data:image/png;base64,([^"]+)"', html)
    assert encoded is not None
    assert base64.b64decode(encoded.group(1)) == PNG
    assert result.warnings == ()


def test_clean_pandoc_generated_document_has_no_warnings(tmp_path, pandoc_docx):
    path = tmp_path / "sample.docx"
    path.write_bytes(pandoc_docx)
    result = convert_to_html(path, strict=True)
    assert result.warnings == ()
    html = result.output_path.read_text(encoding="utf-8")
    assert "Café — 日本語" in html
    assert 'href="https://example.com/path?a=1&amp;b=2"' in html
    assert "<table" in html
    assert "A footnote." in html


def test_tracked_changes_are_accepted_and_comments_dropped(make_docx):
    path = make_docx(
        body=para(
            '<w:del w:id="1" w:author="A"><w:r><w:delText>DELETED</w:delText></w:r></w:del>',
            '<w:ins w:id="2" w:author="A">',
            run("INSERTED"),
            "</w:ins>",
        ),
        files={
            "word/comments.xml": (
                '<?xml version="1.0"?><w:comments xmlns:w="http://schemas.openxmlformats.org/'
                'wordprocessingml/2006/main"><w:comment w:id="0" w:author="REVIEWER"><w:p><w:r>'
                "<w:t>COMMENT</w:t></w:r></w:p></w:comment></w:comments>"
            )
        },
    )
    html = convert_to_html(path).output_path.read_text(encoding="utf-8")
    assert "INSERTED" in html
    assert "DELETED" not in html
    assert "COMMENT" not in html
    assert "REVIEWER" not in html


def test_document_properties_are_not_published(tmp_path):
    core = (
        f'{XML}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
        'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:creator>META-AUTHOR</dc:creator></cp:coreProperties>"
    )
    path = tmp_path / "doc.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            CONTENT_TYPES.replace(
                "</Types>",
                '<Override PartName="/docProps/core.xml" ContentType="application/vnd.'
                'openxmlformats-package.core-properties+xml"/></Types>',
            ),
        )
        archive.writestr(
            "_rels/.rels",
            f'{XML}<Relationships xmlns="{PKG_REL}"><Relationship Id="rId1" '
            f'Type="{REL}/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" '
            f'Type="{PKG_REL}/metadata/core-properties" Target="docProps/core.xml"/>'
            "</Relationships>",
        )
        archive.writestr("docProps/core.xml", core)
        archive.writestr(
            "word/document.xml",
            f"{XML}<w:document {NS}><w:body>{para(run('x'))}</w:body></w:document>",
        )
    html = convert_to_html(path).output_path.read_text(encoding="utf-8")
    assert "META-AUTHOR" not in html


def test_linked_images_warn_because_they_are_dropped(make_docx):
    path = make_docx(
        body=para(image('r:link="ext"')),
        rels=relationship("ext", "image", "https://example.com/pic.png", external=True),
    )
    result = convert_to_html(path, strict=True)  # A warning, never a strict refusal.
    assert [finding.kind for finding in result.warnings] == [FindingKind.LINKED_IMAGE]
    assert "<img" not in body_of(result.output_path.read_text(encoding="utf-8"))


def test_linked_images_are_never_fetched_or_read_from_disk(make_docx, tmp_path):
    secret = tmp_path / "secret.png"
    secret.write_bytes(PNG + b"CANARY-SECRET")
    vml = '<w:r><w:pict><v:shape><v:imagedata r:id="{}"/></v:shape></w:pict></w:r>'
    path = make_docx(
        body=para(image('r:link="a"')) + para(vml.format("b")) + para(vml.format("c")),
        rels=(
            relationship("a", "image", f"file://{secret}", external=True)
            + relationship("b", "image", f"file://{secret}", external=True)
            + relationship("c", "image", str(secret), external=True)
        ),
    )
    result = convert_to_html(path)
    raw = result.output_path.read_text(encoding="utf-8")
    assert "<img" not in body_of(raw)
    assert base64.b64encode(b"CANARY-SECRET").decode()[:8] not in raw
    assert any("3 image(s)" in finding.message for finding in result.warnings)


def test_embedded_image_with_a_missing_target_warns(make_docx):
    path = make_docx(
        body=para(image('r:embed="gone"')),
        rels=relationship("gone", "image", "media/not-in-the-archive.png"),
    )
    result = convert_to_html(path)
    assert [finding.kind for finding in result.warnings] == [FindingKind.LINKED_IMAGE]
    assert "<img" not in body_of(result.output_path.read_text(encoding="utf-8"))


def test_returns_a_result_and_prints_nothing(make_docx, capsys):
    path = make_docx(body=para(run("x")))
    result = convert_to_html(path)
    assert isinstance(result, ConversionResult)
    assert result.output_path == Path(path).with_suffix(".html")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize("as_type", [str, Path])
def test_accepts_str_and_path(make_docx, as_type):
    path = make_docx(body=para(run("x")))
    assert convert_to_html(as_type(path)).output_path.exists()
