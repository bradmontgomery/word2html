#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Setup script for python-bitbucket."""

from setuptools import setup
from word2html import VERSION

SHORT_DESC = (
    "A quick and dirty script to convert a Word "
    "(docx) document to html."
)

setup(
    name='word2html',
    version=VERSION,
    description=SHORT_DESC,
    long_description=open('README.md').read(),
    keywords='word to html',
    author='Brad Montgomery',
    author_email='brad@bradmontgomery.net',
    url='https://github.com/bradmontgomery/word2html',
    license='MIT',
    packages=['word2html'],
    include_package_data=True,
    package_data={'': ['README.md', 'LICENSE.txt']},
    zip_safe=False,
    install_requires=[
        'pypandoc',
        'pytidylib',
    ],
    entry_points={
        'console_scripts': [
            'word2html = word2html.main:main',
        ],
    },
    # Get strings from http://pypi.python.org/pypi?%3Aaction=list_classifiers
    classifiers=[
        'Development Status :: 3 - Alpha',
        'License :: OSI Approved :: MIT License',
        'Intended Audience :: Developers',
        'Topic :: Text Processing',
        'Topic :: Utilities',
    ],
)
