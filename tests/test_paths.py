"""Regressions for pypandoc treating paths as wildcards, which converted the wrong file."""

from __future__ import annotations

import pytest
from conftest import docx_bytes, para, run

from word2html import convert_to_html
from word2html.main import main


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(docx_bytes(para(run(text))))
    return path


def output_of(path):
    return path.with_suffix(".html").read_text(encoding="utf-8")


def test_brackets_never_select_a_similarly_named_sibling(tmp_path):
    wanted = write(tmp_path / "report[1].docx", "WANTED")
    write(tmp_path / "report1.docx", "WRONG-FILE")
    convert_to_html(wanted)
    assert "WANTED" in output_of(wanted)
    assert "WRONG-FILE" not in output_of(wanted)


def test_brackets_without_a_sibling_do_not_crash(tmp_path):
    wanted = write(tmp_path / "report[1].docx", "WANTED")
    convert_to_html(wanted)
    assert "WANTED" in output_of(wanted)


def test_question_mark_never_merges_siblings(tmp_path):
    question = write(tmp_path / "q?.docx", "QMARK")
    write(tmp_path / "q1.docx", "ONE")
    convert_to_html(question)
    html = output_of(question)
    assert "QMARK" in html
    assert "ONE" not in html


def test_star_never_merges_siblings(tmp_path):
    star = write(tmp_path / "s*.docx", "STAR")
    write(tmp_path / "s1.docx", "ONE")
    convert_to_html(star)
    assert "STAR" in output_of(star)
    assert "ONE" not in output_of(star)


def test_bracketed_working_directory_never_selects_another_directory(tmp_path, monkeypatch):
    wanted = write(tmp_path / "proj[1]" / "a.docx", "WANTED")
    write(tmp_path / "proj1" / "a.docx", "OTHER-DIRECTORY")
    monkeypatch.chdir(wanted.parent)
    assert main(["a.docx"]) == 0
    html = (wanted.parent / "a.html").read_text(encoding="utf-8")
    assert "WANTED" in html
    assert "OTHER-DIRECTORY" not in html


def test_bracketed_directory_in_the_path(tmp_path):
    wanted = write(tmp_path / "docs[2024]" / "a.docx", "WANTED")
    convert_to_html(wanted)
    assert "WANTED" in output_of(wanted)


def test_uppercase_extension(tmp_path):
    wanted = write(tmp_path / "UPPER.DOCX", "WANTED")
    result = convert_to_html(wanted)
    assert result.output_path == tmp_path / "UPPER.html"
    assert "WANTED" in result.output_path.read_text(encoding="utf-8")


def test_spaces_and_parentheses(tmp_path):
    wanted = write(tmp_path / "my report (1).docx", "WANTED")
    convert_to_html(wanted)
    assert "WANTED" in output_of(wanted)


@pytest.mark.parametrize("name", ["--lua-filter=evil.docx", "-o.docx", "--output=pwned.docx"])
def test_filenames_are_never_read_as_pandoc_options(tmp_path, monkeypatch, name):
    wanted = write(tmp_path / name, "WANTED")
    monkeypatch.chdir(tmp_path)
    assert main([f"./{name}"]) == 0
    assert "WANTED" in output_of(wanted)
    assert not (tmp_path / "pwned").exists()


def test_filename_starting_with_dashes_after_separator(tmp_path, monkeypatch):
    wanted = write(tmp_path / "--strict.docx", "WANTED")
    monkeypatch.chdir(tmp_path)
    assert main(["--", "--strict.docx"]) == 0
    assert "WANTED" in output_of(wanted)


def test_dotted_filename_keeps_inner_dots(tmp_path):
    wanted = write(tmp_path / "v1.2.final.docx", "WANTED")
    assert convert_to_html(wanted).output_path == tmp_path / "v1.2.final.html"
