"""Convert a Word .docx file to HTML, safely.

Copyright (c) 2025-2026, Brad Montgomery <brad@bradmontgomery.net>
Licensed under the MIT License. See LICENSE.txt for details.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import stat
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from .common import (
    GIB,
    MIB,
    STRICT_REFUSES,
    Finding,
    FindingKind,
    Limits,
    __version__,
    format_size,
    sanitize,
)
from .docx import inspect_docx
from .errors import OutputError, UnsafeDocumentError, WordToHtmlError
from .html_scan import scan_html
from .pandoc import run_pandoc

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 3
EXIT_INTERRUPTED = 130

_SIZE_PATTERN = re.compile(r"^\s*(\d+)\s*([kmg]?)(?:i?b)?\s*$", re.IGNORECASE)
_SIZE_UNITS = {"": 1, "k": 1024, "m": MIB, "g": GIB}
_MIN_MEMORY = 16 * MIB
_MAX_ERROR_CHARS = 1000


@dataclass(frozen=True)
class ConversionResult:
    """The outcome of a successful conversion."""

    output_path: Path
    warnings: tuple[Finding, ...]


def _write_atomic(output_path: Path, html: str) -> None:
    """Replace output_path with new content in one step.

    A failure never leaves a partial file or damages an existing one. A symlink at
    output_path is replaced itself rather than written through to its target.
    """
    data = html.encode("utf-8")
    temp_path = output_path.parent / f".word2html-{secrets.token_hex(8)}.tmp"
    try:
        try:
            existing = output_path.lstat()
        except FileNotFoundError:
            existing = None
        if existing is not None and stat.S_ISDIR(existing.st_mode):
            raise OutputError(f"Output path is a directory: {sanitize(output_path)}")

        # os.open rather than mkstemp: new files get normal umask permissions, not
        # 0600. O_EXCL also guarantees this can't follow a planted symlink.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        descriptor = os.open(temp_path, flags, 0o666)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if existing is not None and stat.S_ISREG(existing.st_mode):
                os.chmod(temp_path, stat.S_IMODE(existing.st_mode))
            os.replace(temp_path, output_path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise OutputError(f"Could not write {sanitize(output_path)}: {sanitize(exc)}") from exc


def convert_to_html(
    filename: str | os.PathLike[str],
    *,
    strict: bool = False,
    limits: Limits | None = None,
) -> ConversionResult:
    """Convert a Word document to HTML next to the original file.

    An existing .html file with the same name is replaced.

    Args:
        filename: Path to the .docx file.
        strict: Refuse documents with unsafe links or hidden text instead of
            returning them as warnings.
        limits: Resource limits, or None for the defaults.

    Returns:
        The output path and any warnings about the document.

    Raises:
        FileNotFoundError: The input doesn't exist.
        UnsafeDocumentError: The document was refused. No output was written.
        WordToHtmlError: The input is invalid, or conversion failed.
    """
    limits = limits or Limits()
    input_path = Path(filename)

    inspection = inspect_docx(input_path, limits)
    html, pandoc_warnings = run_pandoc(input_path, limits, embed_resources=inspection.has_media)
    unsafe_links, active_content = scan_html(html)
    findings = [*inspection.findings, *unsafe_links, *pandoc_warnings]

    if active_content:
        raise UnsafeDocumentError(
            "The document contains active content, so it was not converted.",
            active_content,
        )
    if strict:
        refused = [finding for finding in findings if finding.kind in STRICT_REFUSES]
        if refused:
            raise UnsafeDocumentError(
                "Refusing to convert because --strict is set and the document is not clean.",
                refused,
            )

    output_path = input_path.with_suffix(".html")
    _write_atomic(output_path, html)
    return ConversionResult(output_path, tuple(findings))


def _parse_size(value: str) -> int:
    match = _SIZE_PATTERN.match(value)
    if match is None:
        raise argparse.ArgumentTypeError(f"invalid size {value!r}; try 512M or 2G")
    size = int(match.group(1)) * _SIZE_UNITS[match.group(2).lower()]
    if size <= 0:
        raise argparse.ArgumentTypeError("size must be greater than zero")
    return size


def _parse_memory(value: str) -> int:
    size = _parse_size(value)
    if size < _MIN_MEMORY:
        raise argparse.ArgumentTypeError(
            f"memory limit must be at least {format_size(_MIN_MEMORY)}"
        )
    return size


def _parse_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid number of seconds: {value!r}") from None
    if not 0 < timeout < float("inf"):
        raise argparse.ArgumentTypeError("timeout must be a positive number of seconds")
    return timeout


def _build_parser() -> argparse.ArgumentParser:
    defaults = Limits()
    parser = argparse.ArgumentParser(
        prog="word2html",
        description="Convert a Word document (.docx) to an HTML document.",
        epilog=(
            "exit codes: 0 converted (warnings go to stderr); 1 invalid input or failed "
            "conversion; 2 bad command line; 3 refused for safety."
        ),
    )
    parser.add_argument("path", help="path to your Word document (.docx)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="refuse documents with unsafe links or hidden text instead of warning",
    )
    parser.add_argument(
        "--timeout",
        type=_parse_timeout,
        default=defaults.timeout,
        metavar="SECONDS",
        help="stop pandoc after this long (default: %(default)g)",
    )
    parser.add_argument(
        "--max-memory",
        type=_parse_memory,
        default=defaults.max_memory,
        metavar="SIZE",
        help=f"pandoc memory limit, such as 512M (default: {format_size(defaults.max_memory)})",
    )
    parser.add_argument(
        "--max-file-size",
        type=_parse_size,
        default=defaults.max_file_size,
        metavar="SIZE",
        help=f"largest input file (default: {format_size(defaults.max_file_size)})",
    )
    return parser


def _print_finding(finding: Finding, label: str) -> None:
    print(f"word2html: {label}: {finding.message}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface and return its exit code."""
    args = _build_parser().parse_args(argv)
    limits = replace(
        Limits(),
        timeout=args.timeout,
        max_memory=args.max_memory,
        max_file_size=args.max_file_size,
    )

    try:
        result = convert_to_html(args.path, strict=args.strict, limits=limits)
    except UnsafeDocumentError as exc:
        for finding in exc.findings:
            _print_finding(finding, "refused")
        print(f"word2html: error: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except (WordToHtmlError, OSError) as exc:
        print(f"word2html: error: {sanitize(exc, _MAX_ERROR_CHARS)}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED

    for finding in result.warnings:
        label = "note" if finding.kind is FindingKind.PANDOC else "warning"
        _print_finding(finding, label)
    print(f"Done! Output written to: {sanitize(result.output_path)}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
