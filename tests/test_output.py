"""Output is written atomically and never through a symlink."""

from __future__ import annotations

import os
import stat
import sys

import pytest
from conftest import para, run

from word2html import OutputError, convert_to_html
from word2html.main import main

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file semantics")


def temp_files(directory):
    return [path.name for path in directory.iterdir() if path.name.endswith(".tmp")]


def test_existing_output_is_overwritten(make_docx):
    path = make_docx(body=para(run("NEW")))
    output = path.with_suffix(".html")
    output.write_text("OLD")
    convert_to_html(path)
    html = output.read_text(encoding="utf-8")
    assert "NEW" in html
    assert "OLD" not in html


@posix_only
def test_symlink_is_replaced_and_its_target_left_alone(make_docx, tmp_path):
    path = make_docx(body=para(run("NEW")))
    target = tmp_path / "precious.txt"
    target.write_text("PRECIOUS")
    output = path.with_suffix(".html")
    output.symlink_to(target)
    convert_to_html(path)
    assert not output.is_symlink()
    assert "NEW" in output.read_text(encoding="utf-8")
    assert target.read_text() == "PRECIOUS"


@posix_only
def test_dangling_symlink_is_replaced(make_docx, tmp_path):
    path = make_docx(body=para(run("NEW")))
    output = path.with_suffix(".html")
    missing = tmp_path / "missing.txt"
    output.symlink_to(missing)
    convert_to_html(path)
    assert not output.is_symlink()
    assert not missing.exists()


def test_no_temp_files_left_after_success(make_docx, tmp_path):
    convert_to_html(make_docx(body=para(run("x"))))
    assert temp_files(tmp_path) == []


def test_failed_conversion_keeps_the_existing_output(tmp_path):
    path = tmp_path / "doc.docx"
    path.write_bytes(b"garbage")
    output = path.with_suffix(".html")
    output.write_text("PREVIOUS")
    assert main([str(path)]) == 1
    assert output.read_text() == "PREVIOUS"
    assert temp_files(tmp_path) == []


def test_failed_replace_keeps_output_and_cleans_up(make_docx, tmp_path, monkeypatch):
    path = make_docx(body=para(run("x")))
    output = path.with_suffix(".html")
    output.write_text("PREVIOUS")

    def fail(*_args, **_kwargs):
        raise OSError("disk trouble")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OutputError, match="disk trouble"):
        convert_to_html(path)
    assert output.read_text() == "PREVIOUS"
    assert temp_files(tmp_path) == []


@posix_only
def test_new_file_uses_normal_umask_permissions(make_docx):
    path = make_docx(body=para(run("x")))
    previous = os.umask(0o022)
    try:
        output = convert_to_html(path).output_path
    finally:
        os.umask(previous)
    assert stat.S_IMODE(output.stat().st_mode) == 0o644


@posix_only
def test_overwriting_keeps_the_existing_mode(make_docx):
    path = make_docx(body=para(run("x")))
    output = path.with_suffix(".html")
    output.write_text("OLD")
    output.chmod(0o600)
    convert_to_html(path)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_output_path_that_is_a_directory(make_docx, capsys):
    path = make_docx(body=para(run("x")))
    path.with_suffix(".html").mkdir()
    assert main([str(path)]) == 1
    assert "is a directory" in capsys.readouterr().err


@posix_only
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory permissions"
)
def test_read_only_directory_is_a_clean_error(make_docx, tmp_path, capsys):
    path = make_docx("sub/doc.docx", body=para(run("x")))
    path.parent.chmod(0o500)
    try:
        assert main([str(path)]) == 1
        assert "Could not write" in capsys.readouterr().err
    finally:
        path.parent.chmod(0o700)
