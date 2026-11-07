"""Active content is always refused; legitimate embedded media is not."""

from __future__ import annotations

import re

import pytest
from conftest import image, para, relationship

from word2html import FindingKind, UnsafeDocumentError, convert_to_html
from word2html.html_scan import scan_html
from word2html.main import main

SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" onload="alert(1)">'
    "<script>alert(2)</script></svg>"
)


def media_doc(make_docx, media_name, content):
    return make_docx(
        body=para(image('r:embed="m"')),
        rels=relationship("m", "image", media_name),
        files={f"word/{media_name}": content},
    )


@pytest.mark.parametrize(
    ("media_name", "content"),
    [
        ("media/e.html", "<html><script>alert(1)</script></html>"),
        ("media/e.xhtml", "<html/>"),
        ("media/e.js", "alert(1)"),
    ],
)
@pytest.mark.parametrize("strict", [False, True])
def test_refused_even_without_strict(make_docx, media_name, content, strict):
    path = media_doc(make_docx, media_name, content)
    with pytest.raises(UnsafeDocumentError) as caught:
        convert_to_html(path, strict=strict)
    assert {finding.kind for finding in caught.value.findings} == {FindingKind.ACTIVE_CONTENT}
    assert not path.with_suffix(".html").exists()


def test_cli_exits_three(make_docx, capsys):
    path = media_doc(make_docx, "media/e.html", "<script>alert(1)</script>")
    assert main([str(path)]) == 3
    captured = capsys.readouterr()
    assert "active content" in captured.err
    assert "Done!" not in captured.out


@pytest.mark.parametrize(
    ("media_name", "content", "expected"),
    [
        ("media/e.pdf", b"%PDF-1.4 x", ("embed", "data:application/pdf")),
        ("media/e.mp4", b"x", ("video", "data:video/mp4")),
        ("media/e.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", ("img", "data:image/svg")),
    ],
)
def test_legitimate_media_is_allowed(make_docx, media_name, content, expected):
    path = media_doc(make_docx, media_name, content)
    result = convert_to_html(path, strict=True)
    element, source = expected
    html = result.output_path.read_text(encoding="utf-8")
    assert re.search(rf'<{element}\b[^>]*\bsrc="{source}', html)
    assert result.warnings == ()


def test_malicious_svg_stays_inert_inside_img(make_docx):
    path = media_doc(make_docx, "media/e.svg", SVG)
    result = convert_to_html(path, strict=True)
    html = result.output_path.read_text(encoding="utf-8")
    body = html[html.index("<body") :]
    assert re.search(r'<img\b[^>]*\bsrc="data:image/svg\+xml', body)
    assert "<script" not in body
    assert "onload" not in body


@pytest.mark.parametrize(
    "html",
    [
        "<script>x</script>",
        '<iframe src="https://example.com"></iframe>',
        '<object data="x"></object>',
        '<applet code="x"></applet>',
        '<frameset><frame src="x"></frameset>',
        '<base href="https://example.com/">',
        '<svg><a><animate attributeName="href" values="javascript:alert(1)"/></a></svg>',
        '<meta http-equiv="Refresh" content="0;url=https://example.com">',
        '<img src="x" onerror="alert(1)">',
        '<p ONCLICK="alert(1)">x</p>',
        '<embed src="data:text/html,x">',
        '<embed src="data:image/svg+xml,x">',
        '<embed src="https://example.com/x">',
        '<embed src="e.html">',
        '<video src="data:text/html,x"></video>',
        '<audio src="data:text/html,x"></audio>',
        '<video><source src="data:image/svg+xml,x"></video>',
    ],
)
def test_scanner_flags_active_content(html):
    _, active = scan_html(html)
    assert active


@pytest.mark.parametrize(
    "html",
    [
        '<img src="data:image/png;base64,AAAA">',
        '<img src="data:image/svg+xml;base64,AAAA">',
        '<img src="data:application/octet-stream;base64,AAAA">',
        '<embed src="data:application/pdf;base64,AAAA">',
        '<video src="data:video/mp4;base64,AAAA"></video>',
        '<audio src="data:audio/mpeg;base64,AAAA"></audio>',
        '<meta name="generator" content="pandoc">',
        "<style>a { color: red }</style>",
        "<p>onclick is just a word here</p>",
    ],
)
def test_scanner_allows_inert_content(html):
    links, active = scan_html(html)
    assert not active
    assert not links


def test_data_url_on_a_link_is_only_a_link_warning():
    links, active = scan_html('<a href="data:text/html,x">x</a>')
    assert links
    assert not active
