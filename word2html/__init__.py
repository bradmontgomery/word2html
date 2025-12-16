"""word2html - Convert Word documents to HTML.

Copyright (c) 2025, Brad Montgomery <brad@bradmontgomery.net>
Licensed under the MIT License. See LICENSE.txt for details.
"""

try:
    from importlib.metadata import version, PackageNotFoundError
    try:
        __version__ = version("word2html")
    except PackageNotFoundError:
        __version__ = "0.0.0+dev"
except ImportError:
    __version__ = "0.0.0+dev"

VERSION = __version__

from .main import convert_to_html

__all__ = ["convert_to_html", "__version__", "VERSION"]
