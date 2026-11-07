"""Malformed and hostile documents are rejected with clear errors before pandoc runs."""

from __future__ import annotations

import io
import os
import zipfile

import pytest
from conftest import (
    CONTENT_TYPES,
    NS,
    PKG_REL,
    ROOT_RELS,
    XML,
    docx_bytes,
    para,
    relationship,
    run,
)

from word2html import InvalidDocumentError, convert_to_html
from word2html.main import main


def document_xml(body):
    return f"{XML}<w:document {NS}><w:body>{body}</w:body></w:document>"


def write_zip(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


def valid_members(**overrides):
    members = {
        "[Content_Types].xml": CONTENT_TYPES,
        "_rels/.rels": ROOT_RELS,
        "word/document.xml": document_xml(para(run("x"))),
    }
    members.update(overrides)
    return {name: content for name, content in members.items() if content is not None}


def assert_rejected(path, match):
    with pytest.raises(InvalidDocumentError, match=match):
        convert_to_html(path)
    assert not path.with_suffix(".html").exists()


def test_baseline_helper_builds_a_valid_document(tmp_path):
    path = write_zip(tmp_path / "ok.docx", valid_members())
    assert convert_to_html(path).output_path.exists()


def test_missing_file():
    with pytest.raises(FileNotFoundError):
        convert_to_html("does-not-exist.docx")


def test_directory_named_like_a_docx(tmp_path):
    path = tmp_path / "folder.docx"
    path.mkdir()
    with pytest.raises(InvalidDocumentError, match="Not a regular file"):
        convert_to_html(path)


@pytest.mark.parametrize("name", ["doc.pdf", "doc.doc", "doc", "doc.docm", ".docx"])
def test_wrong_extension(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(docx_bytes(para(run("x"))))
    with pytest.raises(InvalidDocumentError, match=r"Expected a \.docx"):
        convert_to_html(path)


def test_empty_file(tmp_path):
    path = tmp_path / "doc.docx"
    path.write_bytes(b"")
    assert_rejected(path, "empty")


def test_not_a_zip(tmp_path):
    path = tmp_path / "doc.docx"
    path.write_bytes(b"this is plain text, not a document")
    assert_rejected(path, "not a readable zip")


def test_truncated(tmp_path):
    path = tmp_path / "doc.docx"
    data = docx_bytes(para(run("x")))
    path.write_bytes(data[: len(data) // 2])
    assert_rejected(path, "not a readable zip")


def test_password_protected_or_legacy_doc(tmp_path):
    path = tmp_path / "doc.docx"
    path.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"\0" * 600)
    assert_rejected(path, "password-protected or legacy")


def test_corrupted_member_fails_integrity_check(tmp_path):
    path = tmp_path / "doc.docx"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, content in valid_members().items():
            archive.writestr(name, content)
    data = buffer.getvalue().replace(b"<w:body>", b"<w:bodz>", 1)
    path.write_bytes(data)
    assert_rejected(path, "could not read|not well-formed")


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        ("[Content_Types].xml", r"\[Content_Types\].xml is missing"),
        ("_rels/.rels", r"_rels/.rels is missing"),
        ("word/document.xml", "main part word/document.xml is missing"),
    ],
)
def test_missing_required_part(tmp_path, missing, message):
    path = write_zip(tmp_path / "doc.docx", valid_members(**{missing: None}))
    assert_rejected(path, message)


def test_zip_with_unrelated_content(tmp_path):
    path = write_zip(tmp_path / "doc.docx", {"foo.txt": "x"})
    assert_rejected(path, "not a Word document")


def test_no_main_document_relationship(tmp_path):
    rels = f'{XML}<Relationships xmlns="{PKG_REL}"></Relationships>'
    path = write_zip(tmp_path / "doc.docx", valid_members(**{"_rels/.rels": rels}))
    assert_rejected(path, "no main document relationship")


def test_absolute_main_part_target_is_accepted(tmp_path):
    rels = ROOT_RELS.replace('Target="word/document.xml"', 'Target="/word/document.xml"')
    path = write_zip(tmp_path / "doc.docx", valid_members(**{"_rels/.rels": rels}))
    assert convert_to_html(path).output_path.exists()


def test_malformed_document_xml(tmp_path):
    path = write_zip(tmp_path / "doc.docx", valid_members(**{"word/document.xml": "<not-closed"}))
    assert_rejected(path, "not well-formed")


def test_empty_xml_part(tmp_path):
    path = write_zip(tmp_path / "doc.docx", valid_members(**{"word/document.xml": ""}))
    assert_rejected(path, "not well-formed")


def test_malformed_relationships_are_rejected_not_silently_dropped(make_docx):
    # An illegal character here makes pandoc silently drop every link and exit 0.
    target = "https://example.com/&#1;"
    path = make_docx(
        rels=relationship("l", "hyperlink", target, external=True),
    )
    assert_rejected(path, "document.xml.rels is not well-formed")


def test_malformed_xml_in_a_part_pandoc_ignores(tmp_path):
    path = write_zip(tmp_path / "doc.docx", valid_members(**{"customXml/item1.xml": "<a><b></a>"}))
    assert_rejected(path, "customXml/item1.xml is not well-formed")


def test_external_entity_is_rejected(tmp_path, make_docx):
    secret = tmp_path / "secret.txt"
    secret.write_text("CANARY")
    document = (
        f'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file://{secret}">]>'
        f"<w:document {NS}><w:body>{para(run('&x;'))}</w:body></w:document>"
    )
    assert_rejected(make_docx(document=document), "DOCTYPE")


def test_billion_laughs_is_rejected_immediately(make_docx):
    entities = '<!ENTITY a0 "LOL">' + "".join(
        f'<!ENTITY a{n} "{f"&a{n - 1};" * 10}">' for n in range(1, 10)
    )
    document = (
        f'<?xml version="1.0"?><!DOCTYPE d [{entities}]>'
        f"<w:document {NS}><w:body>{para(run('&a9;'))}</w:body></w:document>"
    )
    assert_rejected(make_docx(document=document), "DOCTYPE")


def test_doctype_in_a_relationships_file_is_rejected(tmp_path):
    rels = ROOT_RELS.replace("<Relationships", '<!DOCTYPE d [<!ENTITY a "b">]><Relationships', 1)
    path = write_zip(tmp_path / "doc.docx", valid_members(**{"_rels/.rels": rels}))
    assert_rejected(path, "DOCTYPE")


@pytest.mark.parametrize(
    "name",
    [
        "../evil.png",
        "word/../../evil.png",
        "/etc/evil.png",
        "\\evil.png",
        "C:/evil.png",
        "a\\..\\b",
    ],
)
def test_unsafe_member_paths(tmp_path, name):
    path = write_zip(tmp_path / "doc.docx", valid_members(**{name: "x"}))
    assert_rejected(path, "unsafe path")


def test_duplicate_members_cannot_hide_content_from_the_scan(tmp_path):
    path = tmp_path / "doc.docx"
    members = valid_members()
    hostile = f"{XML}<w:document {NS}><w:body>{para(run('x', '<w:vanish/>'))}</w:body></w:document>"
    with (
        pytest.warns(UserWarning, match="Duplicate name"),
        zipfile.ZipFile(path, "w") as archive,
    ):
        for name, content in members.items():
            archive.writestr(name, content)
        archive.writestr("word/document.xml", hostile)
    assert_rejected(path, "more than one member")


def test_encrypted_member(tmp_path):
    path = write_zip(tmp_path / "doc.docx", valid_members())
    data = bytearray(path.read_bytes())
    # Set the "encrypted" flag in each central directory entry.
    position = data.find(b"PK\x01\x02")
    while position != -1:
        data[position + 8] |= 0x1
        position = data.find(b"PK\x01\x02", position + 4)
    path.write_bytes(bytes(data))
    assert_rejected(path, "encrypted")


def test_binary_media_is_not_parsed_as_xml(make_docx):
    path = make_docx(files={"word/media/image1.png": b"\x89PNG\xff\xfe not xml"})
    assert convert_to_html(path).output_path.exists()


def test_non_utf8_bytes_in_filename_are_safe_in_errors(tmp_path, capsys):
    name = os.fsencode(tmp_path) + b"/bad\xff.docx"
    try:
        with open(name, "wb") as stream:
            stream.write(b"not a zip")
    except OSError:
        pytest.skip("this filesystem refuses non-UTF-8 filenames")
    assert main([os.fsdecode(name)]) == 1
    assert "word2html: error:" in capsys.readouterr().err


def test_cli_reports_a_clean_error_without_a_traceback(tmp_path, capsys):
    path = tmp_path / "doc.docx"
    path.write_bytes(b"garbage")
    assert main([str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("word2html: error:")
    assert "Traceback" not in captured.err
