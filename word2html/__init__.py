"""word2html - Convert Word documents to HTML.

Copyright (c) 2025-2026, Brad Montgomery <brad@bradmontgomery.net>
Licensed under the MIT License. See LICENSE.txt for details.
"""

from .common import Finding, FindingKind, Limits, __version__
from .errors import (
    InvalidDocumentError,
    OutputError,
    PandocError,
    PandocNotFoundError,
    ResourceLimitError,
    UnsafeDocumentError,
    WordToHtmlError,
)
from .main import ConversionResult, convert_to_html

VERSION = __version__

__all__ = [
    "VERSION",
    "ConversionResult",
    "Finding",
    "FindingKind",
    "InvalidDocumentError",
    "Limits",
    "OutputError",
    "PandocError",
    "PandocNotFoundError",
    "ResourceLimitError",
    "UnsafeDocumentError",
    "WordToHtmlError",
    "__version__",
    "convert_to_html",
]
