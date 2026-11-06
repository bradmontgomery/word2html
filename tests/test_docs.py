"""Keeps the documentation site working and in step with the code."""

from __future__ import annotations

import re
from dataclasses import fields
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import word2html
from word2html.common import FindingKind, Limits, format_size
from word2html.html_scan import _ALLOWED_SCHEMES
from word2html.main import _build_parser
from word2html.pandoc import MIN_PANDOC_VERSION

DOCS = Path(__file__).parent.parent / "docs"
PAGES = sorted(DOCS.glob("*.html"))


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.links: list[str] = []
        self.tags: list[str] = []
        self.title = ""
        self.lang = None
        self.text: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
        if tag == "html":
            self.lang = values.get("lang")
        if tag == "a" and "href" in values:
            self.links.append(values["href"])
        if tag == "link" and "href" in values:
            self.links.append(values["href"])
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        self.text.append(data)


def parse(path: Path) -> _Page:
    page = _Page()
    page.feed(path.read_text(encoding="utf-8"))
    page.close()
    return page


def all_text() -> str:
    return " ".join(" ".join(parse(path).text) for path in PAGES)


def page_text(name: str) -> str:
    return " ".join(parse(DOCS / name).text)


def test_site_exists_and_is_served_as_is():
    assert (DOCS / "index.html").exists()
    assert (DOCS / ".nojekyll").exists()
    assert (DOCS / "assets" / "style.css").exists()


@pytest.mark.parametrize("path", PAGES, ids=lambda path: path.name)
def test_page_basics(path):
    page = parse(path)
    assert page.lang == "en"
    assert page.title.strip()
    assert page.tags.count("h1") == 1
    assert len(page.ids) == len(set(page.ids)), "duplicate ids"


@pytest.mark.parametrize("path", PAGES, ids=lambda path: path.name)
def test_pages_load_nothing_from_other_sites_and_run_no_scripts(path):
    source = path.read_text(encoding="utf-8")
    assert "<script" not in source
    assert not re.search(r'<(?:link|img)[^>]+(?:href|src)="(?:https?:)?//', source)


@pytest.mark.parametrize("path", PAGES, ids=lambda path: path.name)
def test_internal_links_and_anchors_resolve(path):
    for link in parse(path).links:
        parts = urlsplit(link)
        if parts.scheme or parts.netloc:
            assert parts.scheme == "https", f"{link} should use https"
            continue
        assert not link.startswith("/"), f"{link} must be relative for GitHub Pages"
        target = (path.parent / parts.path) if parts.path else path
        assert target.exists(), f"{path.name} links to missing {link}"
        if parts.fragment:
            assert parts.fragment in parse(target).ids, f"{path.name} has broken anchor {link}"


def test_every_page_is_in_the_sidebar():
    linked = set(parse(DOCS / "index.html").links)
    for path in PAGES:
        assert path.name in linked


def test_every_cli_option_is_documented():
    usage = page_text("usage.html")
    for action in _build_parser()._actions:
        for option in action.option_strings:
            assert option in usage, f"{option} isn't documented"


def test_documented_option_defaults_match_the_code():
    usage = (DOCS / "usage.html").read_text(encoding="utf-8")
    defaults = Limits()
    expected = {
        "--timeout SECONDS": f"{defaults.timeout:g}",
        "--max-memory SIZE": format_size(defaults.max_memory).replace(" GiB", "G"),
        "--max-file-size SIZE": format_size(defaults.max_file_size).replace(" MiB", "M"),
    }
    for option, value in expected.items():
        assert f"<td><code>{option}</code></td><td><code>{value}</code></td>" in usage


def test_every_limit_is_documented_with_its_default():
    api = page_text("python-api.html")
    defaults = Limits()
    for field in fields(Limits):
        assert field.name in api, f"Limits.{field.name} isn't documented"
    for value in (
        format_size(defaults.max_file_size),
        format_size(defaults.max_total_uncompressed),
        format_size(defaults.max_xml_part_size),
        format_size(defaults.max_memory),
        f"{defaults.max_zip_entries:,}",
        str(defaults.max_xml_depth),
        str(defaults.timeout),
    ):
        assert value in api, f"{value} isn't documented"


def test_public_api_is_documented():
    api = page_text("python-api.html")
    for name in word2html.__all__:
        if name not in {"__version__", "VERSION"}:
            assert name in api, f"{name} isn't documented"
    for kind in FindingKind:
        assert kind.name in api
        assert kind.value in api


def test_allowed_link_schemes_match_the_code():
    security = (DOCS / "security.html").read_text(encoding="utf-8")
    row = re.search(r"<td>Schemes</td><td>(.*?)</td>", security).group(1)
    assert set(re.findall(r"<code>(.*?)</code>", row)) == _ALLOWED_SCHEMES


def test_minimum_pandoc_version_matches_the_code():
    minimum = ".".join(str(part) for part in MIN_PANDOC_VERSION)
    for name in ("index.html", "install.html", "upgrading.html", "troubleshooting.html"):
        assert minimum in page_text(name), f"{name} doesn't state pandoc {minimum}"
    assert "3.1.3 or newer" not in all_text()


def test_ci_tests_the_documented_minimum_pandoc():
    minimum = ".".join(str(part) for part in MIN_PANDOC_VERSION)
    workflow = (DOCS.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert f'PANDOC_MINIMUM: "{minimum}"' in workflow


def test_version_in_docs_matches_the_package():
    pyproject = (DOCS.parent / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'^version = "(.+)"$', pyproject, re.MULTILINE).group(1)
    for path in PAGES:
        assert f"word2html {version}" in " ".join(parse(path).text), path.name


def test_documented_exit_codes_match_the_code():
    from word2html.main import EXIT_ERROR, EXIT_OK, EXIT_REFUSED

    usage = (DOCS / "usage.html").read_text(encoding="utf-8")
    for code in (EXIT_OK, EXIT_ERROR, 2, EXIT_REFUSED):
        assert f"<td><code>{code}</code></td>" in usage


def test_documented_error_messages_exist_in_the_code():
    """A renamed error shouldn't leave troubleshooting advice behind."""
    source = " ".join(
        path.read_text(encoding="utf-8") for path in (DOCS.parent / "word2html").glob("*.py")
    )
    source = re.sub(r'"\s*\n\s*f?"', "", source)
    for message in (
        "pandoc is required but was not found on your PATH",
        "or newer is required",
        "this pandoc build does not allow memory limits",
        "File not found:",
        "Expected a .docx file, got:",
        "Not a regular file:",
        "File is empty:",
        "This is a password-protected or legacy .doc file, not a .docx",
        "it is not a readable zip archive",
        "not a Word document:",
        "is not well-formed XML",
        "contains a DOCTYPE, which Word documents never use",
        "archive member has an unsafe path",
        "archive member is encrypted",
        "archive has more than one member named",
        "pandoc could not read this document",
        "is nested more than",
        "limit for one part",
        "document expands to",
        "Output path is a directory:",
        "Could not write",
        "did not finish within",
        "memory limit",
    ):
        assert message in source, f"{message!r} no longer appears in the code"
