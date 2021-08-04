"""
Quick & dirt script to convert a Word Docx file to HTML.

Requires pypandoc pytidylib  (see requirements.txt)

"""
import argparse
import os
import sys

try:
    import pypandoc
    from tidylib import tidy_document
except ImportError:
    print("\n\nRequires pypandoc and pytidylib. See requirements.txt\n\n")


def convert_to_html(filename):
    # Do the conversion with pandoc
    output = pypandoc.convert(filename, "html")

    # Clean up with tidy...
    output, errors = tidy_document(
        output,
        options={
            "numeric-entities": 1,
            "wrap": 80,
        },
    )
    print(errors)

    # replace smart quotes.
    output = output.replace(u"\u2018", "&lsquo;").replace(u"\u2019", "&rsquo;")
    output = output.replace(u"\u201c", "&ldquo;").replace(u"\u201d", "&rdquo;")

    # write the output
    filename, ext = os.path.splitext(filename)
    filename = "{0}.html".format(filename)
    with open(filename, "w") as f:
        # Python 2 "fix". If this isn't a string, encode it.
        if type(output) is not str:
            output = output.encode("utf-8")
        f.write(output)

    print("Done! Output written to: {}\n".format(filename))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert a Word document to an HTML document."
    )
    parser.add_argument("path", type=str, help="Path to your word document")
    args = parser.parse_args()
    convert_to_html(args.path)
