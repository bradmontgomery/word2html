"""pandoc publishes text hidden in Word, so word2html has to detect it."""

from __future__ import annotations

import pytest
from conftest import footnote_reference, link, para, relationship, run

from word2html import FindingKind, UnsafeDocumentError, convert_to_html
from word2html.main import main

SECRET = "TOPSECRET"
STYLES = (
    '<w:style w:type="character" w:styleId="HiddenChar"><w:rPr><w:vanish/></w:rPr></w:style>'
    '<w:style w:type="character" w:styleId="ChildChar"><w:basedOn w:val="HiddenChar"/></w:style>'
    '<w:style w:type="character" w:styleId="Unhides"><w:basedOn w:val="HiddenChar"/>'
    '<w:rPr><w:vanish w:val="0"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="HiddenPara"><w:rPr><w:vanish/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="ChildPara"><w:basedOn w:val="HiddenPara"/></w:style>'
    '<w:style w:type="character" w:styleId="LoopA"><w:basedOn w:val="LoopB"/></w:style>'
    '<w:style w:type="character" w:styleId="LoopB"><w:basedOn w:val="LoopA"/></w:style>'
    '<w:style w:type="character" w:styleId="Dangling"><w:basedOn w:val="Missing"/></w:style>'
)


def hidden_findings(result):
    return [f for f in result.warnings if f.kind is FindingKind.HIDDEN_TEXT]


@pytest.mark.parametrize(
    "body",
    [
        para(run(SECRET, "<w:vanish/>")),
        para(run(SECRET, '<w:vanish w:val="true"/>')),
        para(run(SECRET, '<w:vanish w:val="1"/>')),
        para(run(SECRET, '<w:rStyle w:val="HiddenChar"/>')),
        para(run(SECRET, '<w:rStyle w:val="ChildChar"/>')),
        para(run(SECRET), ppr='<w:pStyle w:val="HiddenPara"/>'),
        para(run(SECRET), ppr='<w:pStyle w:val="ChildPara"/>'),
        # Direct formatting beats a style that shows the text.
        para(run(SECRET, '<w:rStyle w:val="Unhides"/><w:vanish/>')),
    ],
    ids=["direct", "true", "one", "char-style", "char-based-on", "para", "para-based-on", "direct"],
)
def test_hidden_text_warns(make_docx, body):
    path = make_docx(body=body, styles=STYLES)
    result = convert_to_html(path)
    findings = hidden_findings(result)
    assert len(findings) == 1
    assert "1 run(s)" in findings[0].message
    # Confirms the hazard itself: pandoc really does publish this text.
    assert SECRET in result.output_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "body",
    [
        para(run(SECRET, '<w:vanish w:val="false"/>')),
        para(run(SECRET, '<w:vanish w:val="0"/>')),
        para(run(SECRET, '<w:vanish w:val="off"/>')),
        para(run(SECRET, '<w:rStyle w:val="Unhides"/>')),
        # A direct "off" beats a style that hides the text.
        para(run(SECRET, '<w:rStyle w:val="HiddenChar"/><w:vanish w:val="0"/>')),
        # vanish is a toggle: hiding at both style levels shows the text in Word.
        para(run(SECRET, '<w:rStyle w:val="HiddenChar"/>'), ppr='<w:pStyle w:val="HiddenPara"/>'),
        # Hiding only the paragraph mark doesn't hide the text.
        para(run(SECRET), ppr="<w:rPr><w:vanish/></w:rPr>"),
        para(run(SECRET, "<w:specVanish/>")),
        para(run(SECRET, "<w:webHidden/>")),
        para(run(SECRET, '<w:rStyle w:val="LoopA"/>')),
        para(run(SECRET, '<w:rStyle w:val="Dangling"/>')),
        para(run(SECRET, '<w:rStyle w:val="DoesNotExist"/>')),
        # Hidden formatting with no text has nothing to leak.
        para(run("   ", "<w:vanish/>")),
    ],
)
def test_not_hidden(make_docx, body):
    path = make_docx(body=body, styles=STYLES)
    assert hidden_findings(convert_to_html(path, strict=True)) == []


def test_hidden_inside_a_table(make_docx):
    table = (
        '<w:tbl><w:tblGrid><w:gridCol w:w="1000"/></w:tblGrid><w:tr><w:tc><w:p>'
        + run(SECRET, "<w:vanish/>")
        + "</w:p></w:tc></w:tr></w:tbl>"
    )
    assert hidden_findings(convert_to_html(make_docx(body=table)))


def test_hidden_inside_a_tracked_insertion(make_docx):
    body = para('<w:ins w:id="1" w:author="A">', run(SECRET, "<w:vanish/>"), "</w:ins>")
    assert hidden_findings(convert_to_html(make_docx(body=body)))


def test_hidden_inside_a_footnote(make_docx):
    path = make_docx(
        body=para(run("visible"), footnote_reference()),
        footnotes='<w:footnote w:id="1"><w:p>' + run(SECRET, "<w:vanish/>") + "</w:p></w:footnote>",
    )
    findings = hidden_findings(convert_to_html(path))
    assert len(findings) == 1
    assert "word/footnotes.xml" in findings[0].message


def test_default_paragraph_style_applies_without_pstyle(make_docx):
    styles = (
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        "<w:rPr><w:vanish/></w:rPr></w:style>"
    )
    path = make_docx(body=para(run(SECRET)), styles=styles)
    assert hidden_findings(convert_to_html(path))


def test_document_defaults_apply(make_docx):
    styles = (
        "<w:docDefaults><w:rPrDefault><w:rPr><w:vanish/></w:rPr></w:rPrDefault></w:docDefaults>"
    )
    path = make_docx(body=para(run(SECRET)), styles=styles)
    assert hidden_findings(convert_to_html(path))


def test_counts_every_hidden_run(make_docx):
    body = para(run("a", "<w:vanish/>"), run("visible"), run("b", "<w:vanish/>"))
    findings = hidden_findings(convert_to_html(make_docx(body=body)))
    assert "2 run(s)" in findings[0].message


def test_strict_refuses_and_writes_nothing(make_docx):
    path = make_docx(body=para(run(SECRET, "<w:vanish/>")))
    with pytest.raises(UnsafeDocumentError) as caught:
        convert_to_html(path, strict=True)
    assert [f.kind for f in caught.value.findings] == [FindingKind.HIDDEN_TEXT]
    assert not path.with_suffix(".html").exists()


def test_cli_warning_never_contains_the_hidden_text(make_docx, capsys):
    path = make_docx(body=para(run(SECRET, "<w:vanish/>")))
    assert main([str(path)]) == 0
    captured = capsys.readouterr()
    assert "word2html: warning:" in captured.err
    assert "hidden in Word" in captured.err
    assert SECRET not in captured.err
    assert SECRET not in captured.out


def test_cli_strict_refusal_never_contains_the_hidden_text(make_docx, capsys):
    path = make_docx(body=para(run(SECRET, "<w:vanish/>")))
    assert main(["--strict", str(path)]) == 3
    captured = capsys.readouterr()
    assert SECRET not in captured.err
    assert SECRET not in captured.out


def test_strict_reports_links_and_hidden_text_together(make_docx):
    path = make_docx(
        body=para(run(SECRET, "<w:vanish/>")) + link("l"),
        rels=relationship("l", "hyperlink", "javascript:alert(1)", external=True),
    )
    with pytest.raises(UnsafeDocumentError) as caught:
        convert_to_html(path, strict=True)
    kinds = {finding.kind for finding in caught.value.findings}
    assert kinds == {FindingKind.HIDDEN_TEXT, FindingKind.UNSAFE_LINK}
