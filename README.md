Convert a Word Doc to html
==========================


To install, run:

    pip install word2html

This will give you a command-line script, which you can run:

    $ word2html /path/to/MyGloriousDoc.docx

This will give you a new file, `/path/to/MyGloriousDoc.html`, that's (hopefully)
decent-looking html.


Notes
-----

While this code is MIT-licensed, it uses boty pypandoc and pytidylib, both of
which depend on other software that may not be MIT-licensed and must be installed
for this to work.

* [pytidylib](https://pypi.python.org/pypi/pytidylib/) is available under the
  MIT license, and [Tidy](http://tidy.sourceforge.net/#license) is available
  under an MIT-like license
* [pypandoc](https://pypi.python.org/pypi/pypandoc/) is available under the MIT
  license, while [Pandoc](http://pandoc.org/) is released under the GPL.

