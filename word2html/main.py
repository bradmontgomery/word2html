"""Quick & dirty script to convert a Word Docx file to HTML.

Copyright (c) 2025, Brad Montgomery <brad@bradmontgomery.net>
Licensed under the MIT License. See LICENSE.txt for details.
"""

import argparse
import os
import sys
from pathlib import Path

try:
    import pypandoc
    from tidylib import tidy_document
except ImportError as e:
    print(f"\n\nError: Missing required dependency: {e.name}\n", file=sys.stderr)
    print("Install dependencies with: uv pip install pypandoc pytidylib\n", file=sys.stderr)
    sys.exit(1)


def convert_to_html(filename: str) -> None:
    """Convert a Word document to HTML.
    
    Args:
        filename: Path to the Word document to convert.
        
    Raises:
        FileNotFoundError: If the input file doesn't exist.
        ValueError: If the input file is not a .docx file.
    """
    input_path = Path(filename)
    
    # Validate input file
    if not input_path.exists():
        raise FileNotFoundError(f"File not found: {filename}")
    
    if input_path.suffix.lower() != ".docx":
        raise ValueError(f"Expected .docx file, got: {input_path.suffix}")
    
    # Do the conversion with pandoc
    output = pypandoc.convert_file(str(input_path), "html")

    # Clean up with tidy
    output, errors = tidy_document(
        output,
        options={
            "numeric-entities": 1,
            "wrap": 80,
        },
    )
    
    if errors:
        print(f"Tidy warnings/errors:\n{errors}", file=sys.stderr)

    # Replace smart quotes with HTML entities
    output = output.replace("\u2018", "&lsquo;").replace("\u2019", "&rsquo;")
    output = output.replace("\u201c", "&ldquo;").replace("\u201d", "&rdquo;")

    # Write the output
    output_path = input_path.with_suffix(".html")
    output_path.write_text(output, encoding="utf-8")

    print(f"Done! Output written to: {output_path}")


def main() -> None:
    """Main entry point for the command-line interface."""
    parser = argparse.ArgumentParser(
        description="Convert a Word document to an HTML document."
    )
    parser.add_argument("path", type=str, help="Path to your Word document (.docx)")
    args = parser.parse_args()
    
    try:
        convert_to_html(args.path)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
