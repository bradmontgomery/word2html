"""
Quick & dirt script to convert a Word Docx file to HTML.

Requires pypandoc pytidylib  (see requirements.txt)

"""
import os
import sys
try:
    import pypandoc
    from tidylib import tidy_document
except ImportError:
    print("\n\nRequires pypandoc and pytidylib. See requirements.txt\n\n")


def convert_to_html(filename):
    # Do the conversion with pandoc
    output = pypandoc.convert(filename, 'html')

    # Clean up with tidy...
    output, errors = tidy_document(output,  options={
        'numeric-entities': 1,
        'wrap': 80,
    })
    print(errors)

    # replace smart quotes.
    output = output.replace(u"\u2018", '&lsquo;').replace(u"\u2019", '&rsquo;')
    output = output.replace(u"\u201c", "&ldquo;").replace(u"\u201d", "&rdquo;")

    # write the output
    filename, ext = os.path.splitext(filename)
    filename = "{0}.html".format(filename)
    with open(filename, 'w') as f:
        f.write(output)

    print("Done! Output written to: {}\n".format(filename))


def main():
    if len(sys.argv) == 2:
        convert_to_html(sys.argv[1])
    else:
        print("\nUSAGE: word2html <filename>\n")


if __name__ == "__main__":
    main()
