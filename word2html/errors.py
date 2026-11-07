"""Exceptions raised by word2html.

Copyright (c) 2025-2026, Brad Montgomery <brad@bradmontgomery.net>
Licensed under the MIT License. See LICENSE.txt for details.
"""

from __future__ import annotations

from collections.abc import Sequence

from .common import Finding


class WordToHtmlError(Exception):
    """Base class for every error word2html raises deliberately."""


class InvalidDocumentError(WordToHtmlError, ValueError):
    """The input is not a usable .docx file."""


class ResourceLimitError(WordToHtmlError):
    """The input, or converting it, exceeded a configured limit."""


class PandocNotFoundError(WordToHtmlError):
    """The pandoc executable is not installed or not on PATH."""


class PandocError(WordToHtmlError):
    """pandoc is unusable, or failed for a reason other than the document."""


class OutputError(WordToHtmlError):
    """The converted HTML could not be written."""


class UnsafeDocumentError(WordToHtmlError):
    """The document was refused for safety; no output was written."""

    def __init__(self, message: str, findings: Sequence[Finding] = ()) -> None:
        super().__init__(message)
        self.findings = tuple(findings)
