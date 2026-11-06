"""Find and run the pandoc executable.

Copyright (c) 2025-2026, Brad Montgomery <brad@bradmontgomery.net>
Licensed under the MIT License. See LICENSE.txt for details.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .common import Finding, FindingKind, Limits, sanitize
from .errors import InvalidDocumentError, PandocError, PandocNotFoundError, ResourceLimitError

# Before 3.6, --sandbox did not apply to --embed-resources, so an image URL such as
# /etc/passwd could copy that file into the HTML.
MIN_PANDOC_VERSION = (3, 6)

_INSTALL_HINT = (
    "pandoc is required but was not found on your PATH. "
    "Install it (for example `brew install pandoc`), or see https://pandoc.org/installing.html"
)
_VERSION_PATTERN = re.compile(r"^pandoc(?:\.exe)?\s+(\d+(?:\.\d+)*)", re.MULTILINE)
_VERSION_TIMEOUT_SECONDS = 30.0
# Printed on every conversion by pandoc before 3.6.4, though the title just defaults to the
# filename. The message's continuation lines are indented.
_MISSING_TITLE_WARNING = re.compile(
    r"^\[WARNING\] This document format requires a nonempty <title> element\.\n(?:[ \t]+.*\n?)*",
    re.MULTILINE,
)

# GHC runtime exit code for heap exhaustion, seen when pandoc hits its -M limit.
_EXIT_HEAP_EXHAUSTED = 251
# pandoc's exit codes for input it can't read: PandocSomeError (used for invalid docx
# files), PandocParseError and PandocUTF8DecodingError.
_EXIT_UNREADABLE_DOCUMENT = frozenset({63, 64, 92})


def _format_version(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def find_pandoc() -> str:
    """Locate pandoc and confirm it's new enough."""
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        raise PandocNotFoundError(_INSTALL_HINT)

    try:
        result = subprocess.run(
            [pandoc, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=_VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PandocError(f"could not run pandoc: {sanitize(exc)}") from exc

    match = _VERSION_PATTERN.search(result.stdout)
    if result.returncode != 0 or match is None:
        raise PandocError(f"could not determine the pandoc version at {sanitize(pandoc)}")

    version = tuple(int(part) for part in match.group(1).split("."))
    if version < MIN_PANDOC_VERSION:
        raise PandocError(
            f"pandoc {_format_version(MIN_PANDOC_VERSION)} or newer is required, but "
            f"{sanitize(pandoc)} is version {_format_version(version)}. "
            "See https://pandoc.org/installing.html"
        )
    return pandoc


def run_pandoc(
    path: Path, limits: Limits | None = None, *, embed_resources: bool = True
) -> tuple[str, list[Finding]]:
    """Convert a validated .docx file to standalone HTML.

    --embed-resources re-parses the whole page and uses about six times the memory,
    so callers should turn it off for documents with nothing to embed.

    Returns the HTML and any warnings pandoc printed.
    """
    limits = limits or Limits()
    pandoc = find_pandoc()

    # The absolute path matters: pandoc can't mistake a file named
    # "--lua-filter=evil.docx" for an option, however the user spelled the path.
    command = [
        pandoc,
        "+RTS",
        f"-M{limits.max_memory}",
        "-RTS",
        "--from=docx",
        "--to=html",
        "--standalone",
        *(["--embed-resources"] if embed_resources else []),
        "--sandbox",
        str(path.resolve()),
    ]
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=limits.timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ResourceLimitError(
            f"pandoc did not finish within {limits.timeout:g} seconds; "
            "use --timeout to allow longer"
        ) from exc
    except OSError as exc:
        raise PandocError(f"could not run pandoc: {sanitize(exc)}") from exc

    stderr = result.stderr.strip()
    if result.returncode == _EXIT_HEAP_EXHAUSTED:
        raise ResourceLimitError(
            f"pandoc exceeded its {limits.max_memory}-byte memory limit; "
            "use --max-memory to allow more"
        )
    if result.returncode in _EXIT_UNREADABLE_DOCUMENT:
        raise InvalidDocumentError(f"pandoc could not read this document: {sanitize(stderr)}")
    if result.returncode != 0:
        if "RTS options are disabled" in stderr:
            raise PandocError(
                "this pandoc build does not allow memory limits. "
                "Install an official build from https://pandoc.org/installing.html"
            )
        raise PandocError(f"pandoc failed (exit {result.returncode}): {sanitize(stderr)}")

    warnings = [
        Finding(FindingKind.PANDOC, f"pandoc: {sanitize(line)}")
        for line in _MISSING_TITLE_WARNING.sub("", stderr).splitlines()
        if line.strip()
    ]
    return result.stdout, warnings
