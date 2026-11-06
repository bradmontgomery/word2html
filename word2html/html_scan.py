"""Scan converted HTML for unsafe links and active content.

pandoc does not sanitize its output: its manual says the HTML "is not guaranteed
to be safe". This scan runs on the final HTML, so it sees what a browser would see.

Copyright (c) 2025-2026, Brad Montgomery <brad@bradmontgomery.net>
Licensed under the MIT License. See LICENSE.txt for details.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from .common import Finding, FindingKind, sanitize

_ALLOWED_SCHEMES = frozenset({"http", "https", "mailto", "tel", "ftp"})
_URL_ATTRS = frozenset(
    {"href", "src", "action", "formaction", "poster", "data", "background", "xlink:href"}
)
# An inline <svg> can run code, unlike an SVG in <img>. Attributes aren't enough to judge it:
# <animate> can set a link's href to javascript: without either appearing in an href.
_ACTIVE_ELEMENTS = frozenset(
    {"script", "iframe", "object", "applet", "frame", "frameset", "base", "svg"}
)
_MEDIA_ELEMENTS = frozenset({"video", "audio", "source", "track"})

_SCHEME = re.compile(r"^([a-z][a-z0-9+.\-]*):", re.IGNORECASE)
_DATA_TYPE = re.compile(r"^data:([^;,]*)", re.IGNORECASE)
_STRIP_CHARS = "".join(chr(code) for code in range(0x21))
_REMOVED_CHARS = str.maketrans("", "", "\t\n\r")

# Stops a document with a million bad links from flooding the terminal.
MAX_REPORTED_PER_KIND = 20


def normalize_url(url: str) -> str:
    """Normalize a URL as browsers do before they look at its scheme."""
    return url.strip(_STRIP_CHARS).translate(_REMOVED_CHARS)


def url_scheme(url: str) -> str | None:
    """Return the lowercase scheme of a normalized URL, or None if relative."""
    match = _SCHEME.match(url)
    return match.group(1).lower() if match else None


def _data_type(url: str) -> str:
    match = _DATA_TYPE.match(url)
    return match.group(1).strip().lower() if match else ""


class _Scanner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.unsafe_links: dict[str, None] = {}
        self.active_content: dict[str, None] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _ACTIVE_ELEMENTS:
            self.active_content[f"<{tag}> element"] = None
        if tag == "meta" and self._is_refresh(attrs):
            self.active_content["<meta> refresh"] = None
        for name, value in attrs:
            if name.startswith("on"):
                self.active_content[f'"{name}" event handler on <{tag}>'] = None
            elif name in _URL_ATTRS and value is not None:
                self._check_url(tag, name, value)

    @staticmethod
    def _is_refresh(attrs: list[tuple[str, str | None]]) -> bool:
        return any(
            name == "http-equiv" and value is not None and value.strip().lower() == "refresh"
            for name, value in attrs
        )

    def _check_url(self, tag: str, attr: str, value: str) -> None:
        url = normalize_url(value)
        scheme = url_scheme(url)

        if tag == "embed":
            # A browser runs whatever an <embed> points at. Only inline PDFs are inert.
            if scheme != "data" or _data_type(url) != "application/pdf":
                self.active_content[f"<embed> of {sanitize(url, 80)}"] = None
            return
        if tag == "img" and attr == "src" and scheme == "data":
            return  # Browsers don't run scripts in images, even SVG ones.
        if tag in _MEDIA_ELEMENTS and scheme == "data":
            if not _data_type(url).startswith(("video/", "audio/")):
                self.active_content[f"<{tag}> of {sanitize(url, 80)}"] = None
            return
        if scheme is not None and scheme not in _ALLOWED_SCHEMES:
            self.unsafe_links[url] = None


def _capped(kind: FindingKind, messages: list[str]) -> list[Finding]:
    findings = [Finding(kind, message) for message in messages[:MAX_REPORTED_PER_KIND]]
    extra = len(messages) - MAX_REPORTED_PER_KIND
    if extra > 0:
        findings.append(Finding(kind, f"...and {extra} more"))
    return findings


def scan_html(html: str) -> tuple[list[Finding], list[Finding]]:
    """Scan HTML, returning (unsafe link findings, active content findings)."""
    scanner = _Scanner()
    scanner.feed(html)
    scanner.close()
    links = [f"unsafe link: {sanitize(url)}" for url in scanner.unsafe_links]
    active = [f"active content: {sanitize(item)}" for item in scanner.active_content]
    return (
        _capped(FindingKind.UNSAFE_LINK, links),
        _capped(FindingKind.ACTIVE_CONTENT, active),
    )
