"""Command-line behaviour, package structure, and pandoc failure handling."""

from __future__ import annotations

import subprocess
import sys
from importlib import metadata
from pathlib import Path

import pytest
from conftest import para, run

import word2html
from word2html import PandocError, PandocNotFoundError, ResourceLimitError, convert_to_html
from word2html.common import format_size, sanitize
from word2html.main import _parse_memory, _parse_size, _parse_timeout, main
from word2html.pandoc import MIN_PANDOC_VERSION

VERSION_OK = 'if [ "$1" = "--version" ]; then echo "pandoc 3.13"; exit 0; fi\n'


def test_success_exit_code_and_message(make_docx, capsys):
    path = make_docx(body=para(run("x")))
    assert main([str(path)]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("Done! Output written to:")
    assert captured.err == ""


def test_missing_file_exit_code(capsys):
    assert main(["does-not-exist.docx"]) == 1
    assert "File not found" in capsys.readouterr().err


def test_no_arguments_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0
    assert word2html.__version__ in capsys.readouterr().out


def test_help_documents_the_flags(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    output = capsys.readouterr().out
    for flag in ("--strict", "--timeout", "--max-memory", "--max-file-size"):
        assert flag in output
    assert "exit codes" in output


@pytest.mark.parametrize(
    "args",
    [
        ["--timeout", "0"],
        ["--timeout", "-1"],
        ["--timeout", "soon"],
        ["--timeout", "inf"],
        ["--timeout", "nan"],
        ["--max-memory", "1K"],
        ["--max-memory", "lots"],
        ["--max-file-size", "0"],
    ],
)
def test_invalid_option_values_are_usage_errors(args):
    with pytest.raises(SystemExit) as caught:
        main([*args, "x.docx"])
    assert caught.value.code == 2


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("100", 100),
        ("1K", 1024),
        ("512M", 512 * 1024**2),
        ("2g", 2 * 1024**3),
        ("2GiB", 2 * 1024**3),
        ("10 MB", 10 * 1024**2),
    ],
)
def test_parse_size(value, expected):
    assert _parse_size(value) == expected


def test_parse_memory_and_timeout():
    assert _parse_memory("64M") == 64 * 1024**2
    assert _parse_timeout("1.5") == 1.5


def test_format_size():
    assert format_size(2 * 1024**3) == "2 GiB"
    assert format_size(64 * 1024**2) == "64 MiB"
    assert format_size(10) == "10 bytes"


def test_sanitize_escapes_controls_and_bidi_overrides():
    cleaned = sanitize("a\x1b[31mb\nc‮d\udcff")
    assert cleaned == "a\\x1b[31mb\\nc\\u202ed\\udcff"


def test_sanitize_leaves_ordinary_unicode_alone():
    assert sanitize("Café 日本語 report (1).docx") == "Café 日本語 report (1).docx"


def test_runs_as_a_real_subprocess(make_docx):
    path = make_docx(body=para(run("x")))
    result = subprocess.run(
        [sys.executable, "-m", "word2html.main", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert path.with_suffix(".html").exists()


def test_import_does_not_exit_when_nothing_else_is_installed():
    """Importing used to call sys.exit() when a dependency was missing."""
    code = (
        "import sys\n"
        "sys.modules['pypandoc'] = None\n"
        "sys.modules['tidylib'] = None\n"
        "import word2html\n"
        "print('imported', word2html.__version__)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert "imported" in result.stdout


def test_package_declares_no_python_dependencies():
    assert not metadata.requires("word2html")


def test_public_api_is_exported():
    for name in word2html.__all__:
        assert hasattr(word2html, name)
    assert word2html.__version__ == word2html.VERSION


def test_pandoc_missing(make_docx, monkeypatch, tmp_path, capsys):
    path = make_docx(body=para(run("x")))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(PandocNotFoundError, match="installing"):
        convert_to_html(path)
    assert main([str(path)]) == 1
    assert "pandoc is required" in capsys.readouterr().err


@pytest.mark.parametrize("version", ["pandoc 2.19.2", "pandoc 3.1.3", "pandoc 3.1.11", "pandoc 3"])
def test_pandoc_too_old(make_docx, stub_pandoc, version):
    stub_pandoc(f'echo "{version}"')
    with pytest.raises(PandocError, match=r"3\.1\.11\.1 or newer"):
        convert_to_html(make_docx(body=para(run("x"))))


@pytest.mark.parametrize(
    "version", ["pandoc 3.1.11.1", "pandoc 3.1.12", "pandoc 10.0", "pandoc.exe 3.13"]
)
def test_pandoc_new_enough(make_docx, stub_pandoc, version):
    stub_pandoc(f'if [ "$1" = "--version" ]; then echo "{version}"; exit 0; fi\necho "<p>ok</p>"')
    assert convert_to_html(make_docx(body=para(run("x")))).output_path.exists()


def test_pandoc_version_unrecognized(make_docx, stub_pandoc):
    stub_pandoc('echo "something else"')
    with pytest.raises(PandocError, match="could not determine"):
        convert_to_html(make_docx(body=para(run("x"))))


def test_pandoc_timeout_is_a_clean_error(make_docx, stub_pandoc, capsys):
    stub_pandoc(VERSION_OK + "exec sleep 30")
    path = make_docx(body=para(run("x")))
    assert main(["--timeout", "0.2", str(path)]) == 1
    assert "did not finish within 0.2 seconds" in capsys.readouterr().err
    assert not path.with_suffix(".html").exists()


def test_pandoc_heap_exhaustion_exit_code(make_docx, stub_pandoc):
    stub_pandoc(VERSION_OK + "exit 251")
    with pytest.raises(ResourceLimitError, match="memory limit"):
        convert_to_html(make_docx(body=para(run("x"))))


def test_pandoc_build_without_rts_options(make_docx, stub_pandoc):
    stub_pandoc(VERSION_OK + 'echo "Most RTS options are disabled." >&2\nexit 1')
    with pytest.raises(PandocError, match="does not allow memory limits"):
        convert_to_html(make_docx(body=para(run("x"))))


def test_pandoc_generic_failure_sanitizes_stderr(make_docx, stub_pandoc, capsys):
    stub_pandoc(VERSION_OK + r'printf "boom\033[31m\n" >&2' + "\nexit 7")
    path = make_docx(body=para(run("x")))
    assert main([str(path)]) == 1
    err = capsys.readouterr().err
    assert "exit 7" in err
    assert "\x1b" not in err


def test_pandoc_warnings_pass_through_as_notes(make_docx, stub_pandoc, capsys):
    stub_pandoc(VERSION_OK + 'echo "[WARNING] something" >&2\necho "<p>ok</p>"')
    path = make_docx(body=para(run("x")))
    assert main(["--strict", str(path)]) == 0  # A note isn't a strict refusal.
    assert "word2html: note: pandoc: [WARNING] something" in capsys.readouterr().err


def test_missing_title_warning_is_filtered_but_other_warnings_are_kept(
    make_docx, stub_pandoc, capsys
):
    stub_pandoc(
        VERSION_OK
        + "cat >&2 <<'MSG'\n"
        + "[WARNING] This document format requires a nonempty <title> element.\n"
        + "  Defaulting to 'doc' as the title.\n"
        + "  To specify a title, use 'title' in metadata or --metadata title=\"...\".\n"
        + "[WARNING] Something else\n"
        + "MSG\n"
        + 'echo "<p>ok</p>"'
    )
    assert main([str(make_docx(body=para(run("x"))))]) == 0
    err = capsys.readouterr().err
    assert "title" not in err
    assert "[WARNING] Something else" in err


def test_pandoc_cannot_run(make_docx, stub_pandoc, tmp_path):
    stub_pandoc(VERSION_OK)
    stub = tmp_path / "stub-bin" / "pandoc"
    stub.write_bytes(b"\x00not a program")
    with pytest.raises(PandocError):
        convert_to_html(make_docx(body=para(run("x"))))


def test_readme_documents_what_the_cli_does():
    """Keeps the README honest about the behaviour users rely on."""
    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    for required in (
        "--strict",
        "--timeout",
        "--max-memory",
        "--max-file-size",
        "brew install pandoc",
        ".".join(str(part) for part in MIN_PANDOC_VERSION),
    ):
        assert required in readme
    assert "NO TESTS" not in readme
