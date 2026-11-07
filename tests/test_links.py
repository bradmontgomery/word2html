"""Unsafe links warn by default and are refused under --strict."""

from __future__ import annotations

import pytest
from conftest import link, para, relationship, run

from word2html import FindingKind, UnsafeDocumentError, convert_to_html
from word2html.html_scan import MAX_REPORTED_PER_KIND, normalize_url, scan_html, url_scheme
from word2html.main import main

UNSAFE = [
    "javascript:alert(1)",
    "JaVaScRiPt:alert(2)",
    "java&#9;script:alert(3)",
    "java&#10;script:alert(4)",
    " javascript:alert(5)",
    "vbscript:msgbox(6)",
    "data:text/html,x",
    "data:image/png;base64,AAAA",
    "file:///etc/passwd",
    "blob:x",
]
SAFE = [
    "https://example.com/",
    "HTTPS://EXAMPLE.COM/",
    "http://example.com/?a=1&amp;b=2",
    "mailto:a@example.com",
    "tel:+15551234",
    "ftp://example.com/x",
    "//example.com/x",
    "relative/path.html",
    "../up.html",
]


def linked_doc(make_docx, target, name="doc.docx"):
    return make_docx(
        name,
        body=link("l1"),
        rels=relationship("l1", "hyperlink", target, external=True),
    )


@pytest.mark.parametrize("target", UNSAFE)
def test_unsafe_links_warn_and_still_convert(make_docx, target):
    path = linked_doc(make_docx, target)
    result = convert_to_html(path)
    assert result.output_path.exists()
    assert [finding.kind for finding in result.warnings] == [FindingKind.UNSAFE_LINK]


@pytest.mark.parametrize("target", UNSAFE)
def test_strict_refuses_unsafe_links_and_writes_nothing(make_docx, target):
    path = linked_doc(make_docx, target)
    with pytest.raises(UnsafeDocumentError) as caught:
        convert_to_html(path, strict=True)
    assert [finding.kind for finding in caught.value.findings] == [FindingKind.UNSAFE_LINK]
    assert not path.with_suffix(".html").exists()


@pytest.mark.parametrize("target", SAFE)
def test_safe_links_pass_strict_without_warnings(make_docx, target):
    path = linked_doc(make_docx, target)
    result = convert_to_html(path, strict=True)
    assert result.warnings == ()
    assert result.output_path.exists()


def test_internal_anchor_links_are_safe(make_docx):
    path = make_docx(body=para(f'<w:hyperlink w:anchor="sec">{run("x")}</w:hyperlink>'))
    assert convert_to_html(path, strict=True).warnings == ()


def test_word_hyperlink_fields_are_checked(make_docx):
    field = (
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> HYPERLINK "javascript:alert(4)" </w:instrText>'
        '</w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        + run("FIELD")
        + '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )
    path = make_docx(body=para(field))
    with pytest.raises(UnsafeDocumentError):
        convert_to_html(path, strict=True)


def test_quotes_cannot_break_out_of_the_href_attribute(make_docx):
    path = linked_doc(make_docx, "https://example.com/&quot;onmouseover=&quot;alert(1)")
    result = convert_to_html(path)
    html = result.output_path.read_text(encoding="utf-8")
    assert ' onmouseover="' not in html
    assert result.warnings == ()


def test_cli_warns_on_stderr_and_exits_zero(make_docx, capsys):
    path = linked_doc(make_docx, "javascript:alert(1)")
    assert main([str(path)]) == 0
    captured = capsys.readouterr()
    assert "word2html: warning: unsafe link: javascript:alert(1)" in captured.err
    assert "unsafe link" not in captured.out
    assert "Done!" in captured.out
    assert path.with_suffix(".html").exists()


def test_cli_strict_exits_three_and_writes_nothing(make_docx, capsys):
    path = linked_doc(make_docx, "javascript:alert(1)")
    assert main(["--strict", str(path)]) == 3
    captured = capsys.readouterr()
    assert "unsafe link: javascript:alert(1)" in captured.err
    assert "--strict" in captured.err
    assert captured.out == ""
    assert not path.with_suffix(".html").exists()


def test_strict_keeps_an_existing_output_file_intact(make_docx):
    path = linked_doc(make_docx, "javascript:alert(1)")
    output = path.with_suffix(".html")
    output.write_text("PREVIOUS")
    with pytest.raises(UnsafeDocumentError):
        convert_to_html(path, strict=True)
    assert output.read_text() == "PREVIOUS"


def test_terminal_escape_sequences_in_links_are_neutralized():
    links, _ = scan_html('<a href="blob:\x1b[31mRED\x1b]0;TITLE\x07">x</a>')
    assert links
    assert "\x1b" not in links[0].message
    assert "\x07" not in links[0].message
    assert "\\x1b" in links[0].message


def test_cli_output_has_no_terminal_escapes_from_a_document(make_docx, capsys):
    path = linked_doc(make_docx, "blob:&#x85;a")
    assert main([str(path)]) == 0
    assert "\x85" not in capsys.readouterr().err


def test_long_links_are_truncated_in_messages():
    links, _ = scan_html(f'<a href="javascript:{"a" * 5000}">x</a>')
    assert len(links[0].message) < 400
    assert "more characters" in links[0].message


def test_many_bad_links_are_capped():
    html = "".join(f'<a href="javascript:{n}">x</a>' for n in range(100))
    links, _ = scan_html(html)
    assert len(links) == MAX_REPORTED_PER_KIND + 1
    assert links[-1].message == "...and 80 more"


def test_duplicate_links_are_reported_once():
    links, _ = scan_html('<a href="javascript:1">x</a>' * 5)
    assert len(links) == 1


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("java\tscript:x", "javascript:x"),
        ("java\nscr\ript:x", "javascript:x"),
        ("  \x01javascript:x ", "javascript:x"),
    ],
)
def test_normalize_url_matches_browser_behaviour(raw, normalized):
    assert normalize_url(raw) == normalized


@pytest.mark.parametrize(
    ("url", "scheme"),
    [
        ("HTTP://x", "http"),
        ("#a", None),
        ("a/b:c", None),
        ("1a:b", None),
        ("//x:80/", None),
    ],
)
def test_url_scheme(url, scheme):
    assert url_scheme(url) == scheme


def test_unsafe_image_sources_are_reported():
    links, active = scan_html('<img src="file:///etc/passwd">')
    assert links
    assert not active
