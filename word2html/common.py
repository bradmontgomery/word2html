"""Types and helpers shared across word2html.

Copyright (c) 2025-2026, Brad Montgomery <brad@bradmontgomery.net>
Licensed under the MIT License. See LICENSE.txt for details.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("word2html")
except PackageNotFoundError:
    __version__ = "0.0.0+dev"

MIB = 1024 * 1024
GIB = 1024 * MIB

MAX_MESSAGE_CHARS = 200

# Unicode categories that can move the cursor, recolour the terminal, or visually
# reorder text: controls, format characters (bidi overrides), surrogates from
# undecodable filenames, and line/paragraph separators.
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


class FindingKind(StrEnum):
    """What a finding is about."""

    UNSAFE_LINK = "unsafe-link"
    HIDDEN_TEXT = "hidden-text"
    ACTIVE_CONTENT = "active-content"
    LINKED_IMAGE = "linked-image"
    PANDOC = "pandoc"


# Findings that --strict turns into a refusal. Linked images and pandoc's own
# messages stay as warnings: they are about lost content, not safety.
STRICT_REFUSES = frozenset({FindingKind.UNSAFE_LINK, FindingKind.HIDDEN_TEXT})


@dataclass(frozen=True)
class Finding:
    """Something the user should know about a document."""

    kind: FindingKind
    message: str


@dataclass(frozen=True)
class Limits:
    """Resource limits applied to each conversion."""

    max_file_size: int = 200 * MIB
    max_total_uncompressed: int = GIB
    max_xml_part_size: int = 64 * MIB
    max_zip_entries: int = 10_000
    max_xml_depth: int = 256
    max_memory: int = 2 * GIB
    timeout: float = 120.0


def sanitize(text: object, limit: int = MAX_MESSAGE_CHARS) -> str:
    """Make document-derived text safe to print to a terminal.

    Control characters are escaped so a crafted URL or filename can't inject
    terminal escape sequences or forge extra output lines, and long values are
    truncated so one huge data: URL can't flood the screen.
    """
    escaped: list[str] = []
    for char in str(text):
        if unicodedata.category(char) in _UNSAFE_CATEGORIES:
            escaped.append(char.encode("unicode_escape", "backslashreplace").decode("ascii"))
        else:
            escaped.append(char)
    result = "".join(escaped)
    if len(result) > limit:
        return f"{result[:limit]}... ({len(result) - limit} more characters)"
    return result


def format_size(num_bytes: int) -> str:
    """Format a byte count for messages."""
    if num_bytes >= GIB and num_bytes % GIB == 0:
        return f"{num_bytes // GIB} GiB"
    if num_bytes >= MIB:
        return f"{num_bytes / MIB:.0f} MiB"
    return f"{num_bytes} bytes"
