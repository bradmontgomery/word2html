"""Resource limits stop hostile documents quickly and with clean errors."""

from __future__ import annotations

import time
import zipfile
from dataclasses import replace

import pytest
from conftest import docx_bytes, para, run

from word2html import InvalidDocumentError, Limits, ResourceLimitError, convert_to_html
from word2html.common import MIB
from word2html.main import main
from word2html.pandoc import run_pandoc

MAX_PREFLIGHT_SECONDS = 5.0


def nested_tables(depth):
    return (
        "<w:tbl><w:tr><w:tc>" * depth
        + "<w:p><w:r><w:t>x</w:t></w:r></w:p>"
        + "<w:p/></w:tc></w:tr></w:tbl>" * depth
    )


def test_3kb_nested_tables_are_rejected_in_preflight(make_docx):
    """This 3 KB file exhausted a 4 GB heap in pandoc during the security review."""
    path = make_docx(body=nested_tables(5000))
    assert path.stat().st_size < 5000
    started = time.monotonic()
    with pytest.raises(ResourceLimitError, match="nested more than 256 levels"):
        convert_to_html(path)
    assert time.monotonic() - started < MAX_PREFLIGHT_SECONDS
    assert not path.with_suffix(".html").exists()


def test_100mb_of_xml_is_rejected_in_preflight(make_docx):
    """This 409 KB file reached 2.4 GB in pandoc during the security review."""
    unit = para(run("x" * 100))
    path = make_docx(body=unit * (100 * MIB // len(unit)))
    assert path.stat().st_size < 1 * MIB
    started = time.monotonic()
    with pytest.raises(ResourceLimitError, match="64 MiB limit for one part"):
        convert_to_html(path)
    assert time.monotonic() - started < MAX_PREFLIGHT_SECONDS


def test_reasonable_nesting_is_allowed(make_docx):
    path = make_docx(body=nested_tables(20))
    assert convert_to_html(path).output_path.exists()


def test_output_size_no_longer_blows_up(make_docx):
    """Through tidy, 2.4 KB of nested tables became 8 MB of indented HTML."""
    path = make_docx(body=nested_tables(50))
    output = convert_to_html(path).output_path
    assert output.stat().st_size < 1 * MIB


def test_file_size_limit(make_docx):
    path = make_docx(body=para(run("x")))
    limits = replace(Limits(), max_file_size=10)
    with pytest.raises(ResourceLimitError, match=r"File is .* over the"):
        convert_to_html(path, limits=limits)


def test_cli_max_file_size(make_docx, capsys):
    path = make_docx(body=para(run("x")))
    assert main(["--max-file-size", "1K", str(path)]) == 1
    assert "over the" in capsys.readouterr().err


def test_entry_count_limit(make_docx):
    path = make_docx(body=para(run("x")))
    with pytest.raises(ResourceLimitError, match="entries"):
        convert_to_html(path, limits=replace(Limits(), max_zip_entries=2))


def test_total_uncompressed_limit_from_declared_sizes(make_docx):
    path = make_docx(body=para(run("x")), files={"word/media/big.bin": b"\0" * (2 * MIB)})
    with pytest.raises(ResourceLimitError, match="document expands to"):
        convert_to_html(path, limits=replace(Limits(), max_total_uncompressed=MIB))


def _with_declared_size(path, member, declared):
    """Rewrite a member's size fields so its zip headers lie about its real size."""
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo(member)
    data = bytearray(path.read_bytes())
    local = info.header_offset
    data[local + 22 : local + 26] = declared.to_bytes(4, "little")
    position = data.find(b"PK\x01\x02")
    encoded = member.encode()
    while position != -1:
        name_length = int.from_bytes(data[position + 28 : position + 30], "little")
        if bytes(data[position + 46 : position + 46 + name_length]) == encoded:
            data[position + 24 : position + 28] = declared.to_bytes(4, "little")
        position = data.find(b"PK\x01\x02", position + 4)
    path.write_bytes(bytes(data))


def test_zip_headers_that_understate_size_are_rejected(make_docx):
    path = make_docx(body=para(run("x" * 200_000)))
    _with_declared_size(path, "word/document.xml", 100)
    with pytest.raises(InvalidDocumentError, match=r"could not read|not well-formed"):
        convert_to_html(path)


def test_total_limit_counts_xml_parts(make_docx):
    path = make_docx(body=para(run("x")), files={"word/extra.xml": "<a>" + "b" * 1500 + "</a>"})
    with pytest.raises(ResourceLimitError, match="document expands to"):
        convert_to_html(path, limits=replace(Limits(), max_total_uncompressed=1200))


def test_preflight_part_limit_uses_uncompressed_size(make_docx):
    path = make_docx(body=para(run("x" * 3000)))
    with pytest.raises(ResourceLimitError, match="limit for one part"):
        convert_to_html(path, limits=replace(Limits(), max_xml_part_size=1000))


def test_real_pandoc_heap_cap_is_a_clean_error(tmp_path):
    """Calls pandoc directly because preflight would otherwise catch this first."""
    path = tmp_path / "doc.docx"
    unit = para(run("x" * 100))
    path.write_bytes(docx_bytes(unit * (20 * MIB // len(unit))))
    with pytest.raises(ResourceLimitError, match="memory limit"):
        run_pandoc(path, replace(Limits(), max_memory=16 * MIB))


def test_memory_limit_is_passed_to_pandoc(make_docx, stub_pandoc, tmp_path):
    path = make_docx(body=para(run("x")))
    log = tmp_path / "args.log"
    stub_pandoc(
        'if [ "$1" = "--version" ]; then echo "pandoc 3.13"; exit 0; fi\n'
        f'echo "$@" > "{log}"\necho "<html></html>"'
    )
    assert main(["--max-memory", "512M", "--timeout", "5", str(path)]) == 0
    args = log.read_text()
    assert f"+RTS -M{512 * MIB} -RTS" in args
    assert "--sandbox" in args
    assert str(path.resolve()) in args


@pytest.mark.parametrize(
    ("files", "expected"),
    [(None, False), ({"word/media/image1.png": b"x"}, True)],
    ids=["no-media", "media"],
)
def test_embedding_is_only_requested_when_there_is_media(
    make_docx, stub_pandoc, tmp_path, files, expected
):
    """--embed-resources costs about six times the memory, even with nothing to embed."""
    path = make_docx(body=para(run("x")), files=files)
    log = tmp_path / "args.log"
    stub_pandoc(
        'if [ "$1" = "--version" ]; then echo "pandoc 3.13"; exit 0; fi\n'
        f'echo "$@" > "{log}"\necho "<html></html>"'
    )
    convert_to_html(path)
    assert ("--embed-resources" in log.read_text()) is expected
