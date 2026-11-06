"""Shared test helpers. Every test document is built in code, so no binaries are committed."""

from __future__ import annotations

import io
import os
import shutil
import stat
import subprocess
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:v="urn:schemas-microsoft-com:vml"'
)
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
XML = '<?xml version="1.0" encoding="UTF-8"?>'
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c63606060f80f00010401005f2f1d1e"
    "0000000049454e44ae426082"
)
CONTENT_TYPES = (
    f'{XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/></Types>'
)
ROOT_RELS = (
    f'{XML}<Relationships xmlns="{PKG_REL}"><Relationship Id="rId1" '
    f'Type="{REL}/officeDocument" Target="word/document.xml"/></Relationships>'
)


def run(text: str, props: str = "") -> str:
    """A text run."""
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{text}</w:t></w:r>'


def para(*content: str, ppr: str = "") -> str:
    """A paragraph."""
    props = f"<w:pPr>{ppr}</w:pPr>" if ppr else ""
    return f"<w:p>{props}{''.join(content)}</w:p>"


def image(attribute: str) -> str:
    """A DrawingML picture run, such as image('r:embed="rId9"')."""
    return (
        '<w:r><w:drawing><wp:inline><wp:extent cx="914400" cy="914400"/>'
        '<wp:docPr id="1" name="p" descr="ALT"/><a:graphic>'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic><pic:nvPicPr><pic:cNvPr id="0" name="p"/><pic:cNvPicPr/></pic:nvPicPr>'
        f"<pic:blipFill><a:blip {attribute}/></pic:blipFill><pic:spPr/></pic:pic>"
        "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
    )


def relationship(rel_id: str, kind: str, target: str, *, external: bool = False) -> str:
    """A relationship entry for a document's .rels file."""
    mode = ' TargetMode="External"' if external else ""
    return f'<Relationship Id="{rel_id}" Type="{REL}/{kind}" Target="{target}"{mode}/>'


def link(rel_id: str, text: str = "LINK") -> str:
    """A hyperlink paragraph."""
    return para(f'<w:hyperlink r:id="{rel_id}">{run(text)}</w:hyperlink>')


def footnote_reference(footnote_id: int = 1) -> str:
    return f'<w:r><w:footnoteReference w:id="{footnote_id}"/></w:r>'


def docx_bytes(
    body: str = "",
    *,
    rels: str = "",
    styles: str | None = None,
    footnotes: str | None = None,
    files: Mapping[str, str | bytes] | None = None,
    document: str | None = None,
    root_rels: str = ROOT_RELS,
) -> bytes:
    """Build a .docx file in memory."""
    all_rels = rels
    if styles is not None:
        all_rels += relationship("rStyles", "styles", "styles.xml")
    if footnotes is not None:
        all_rels += relationship("rNotes", "footnotes", "footnotes.xml")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr(
            "word/_rels/document.xml.rels",
            f'{XML}<Relationships xmlns="{PKG_REL}">{all_rels}</Relationships>',
        )
        archive.writestr(
            "word/document.xml",
            document
            if document is not None
            else f"{XML}<w:document {NS}><w:body>{body}</w:body></w:document>",
        )
        if styles is not None:
            archive.writestr("word/styles.xml", f"{XML}<w:styles {NS}>{styles}</w:styles>")
        if footnotes is not None:
            archive.writestr(
                "word/footnotes.xml", f"{XML}<w:footnotes {NS}>{footnotes}</w:footnotes>"
            )
        for name, content in (files or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.fixture
def make_docx(tmp_path: Path) -> Callable[..., Path]:
    """Write a built document to a temporary directory and return its path."""

    def build(name: str = "doc.docx", body: str = "", **kwargs: object) -> Path:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(docx_bytes(body, **kwargs))  # type: ignore[arg-type]
        return path

    return build


MARKDOWN_SAMPLE = """\
---
title: Sample
author: A. Writer
---

# Heading

Café — 日本語 “quoted” and a [link](https://example.com/path?a=1&b=2)[^1].

[^1]: A footnote.

| A | B |
|---|---|
| 1 | 2 |

Math: $x^2 + y^2$

1. one
2. two

```python
print("code")
```
"""


@pytest.fixture(scope="session")
def pandoc_docx(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """A realistic document with the structure pandoc's own docx writer produces."""
    directory = tmp_path_factory.mktemp("pandoc_docx")
    output = directory / "sample.docx"
    subprocess.run(
        ["pandoc", "--from=markdown", "--to=docx", f"--output={output}"],
        input=MARKDOWN_SAMPLE,
        encoding="utf-8",
        check=True,
    )
    return output.read_bytes()


@pytest.fixture
def stub_pandoc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Put a fake pandoc first on PATH, for states that are hard to reproduce for real."""

    def install(script: str) -> None:
        bin_dir = tmp_path / "stub-bin"
        bin_dir.mkdir(exist_ok=True)
        stub = bin_dir / "pandoc"
        stub.write_text(f"#!/bin/sh\n{script}\n")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    return install


def pytest_configure(config: pytest.Config) -> None:
    """Fail rather than skip without pandoc, so CI can't pass without running the tests."""
    if shutil.which("pandoc") is None:
        pytest.exit("pandoc must be installed to run the tests", returncode=1)
