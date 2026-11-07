"""Validate a .docx file before pandoc sees it, and find content pandoc mishandles.

pandoc will happily exhaust gigabytes of memory on a 3 KB nested-table document,
silently drops every link when a relationships file is malformed, and publishes
text that Word hides. Everything here runs first, using only the standard library,
with streaming parsers and hard limits.

Copyright (c) 2025-2026, Brad Montgomery <brad@bradmontgomery.net>
Licensed under the MIT License. See LICENSE.txt for details.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from xml.parsers import expat

from .common import Finding, FindingKind, Limits, format_size, sanitize
from .errors import InvalidDocumentError, ResourceLimitError

_OLE2_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_CHUNK_SIZE = 64 * 1024
_ENCRYPTED_FLAG = 0x1

_W_NAMESPACES = frozenset(
    {
        "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "http://purl.oclc.org/ooxml/wordprocessingml/main",
    }
)
_R_NAMESPACES = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "http://purl.oclc.org/ooxml/officeDocument/relationships",
    }
)
_A_NAMESPACES = frozenset(
    {
        "http://schemas.openxmlformats.org/drawingml/2006/main",
        "http://purl.oclc.org/ooxml/drawingml/main",
    }
)
_VML_NAMESPACE = "urn:schemas-microsoft-com:vml"
_RELS_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"
_FALSE_VALUES = frozenset({"false", "0", "off"})
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


class _RejectedXml(Exception):
    """Raised from inside expat callbacks to abort a parse."""


@dataclass(frozen=True)
class DocxInspection:
    """What preflight learned about a document."""

    findings: tuple[Finding, ...]
    has_media: bool


@dataclass(frozen=True)
class _Relationship:
    rel_id: str
    rel_type: str
    target: str
    external: bool


@dataclass
class _Style:
    vanish: bool | None = None
    based_on: str | None = None


@dataclass
class _Styles:
    """Character and paragraph styles, enough to resolve hidden-text formatting."""

    default_vanish: bool = False
    default_paragraph: str | None = None
    paragraph: dict[str, _Style] = field(default_factory=dict)
    character: dict[str, _Style] = field(default_factory=dict)

    def resolve(self, table: dict[str, _Style], style_id: str | None) -> bool:
        """Follow basedOn to the nearest explicit vanish value."""
        seen: set[str] = set()
        while style_id is not None and style_id not in seen:
            seen.add(style_id)
            style = table.get(style_id)
            if style is None:
                return False
            if style.vanish is not None:
                return style.vanish
            style_id = style.based_on
        return False

    def is_hidden(
        self, *, direct: bool | None, character: str | None, paragraph: str | None
    ) -> bool:
        """Work out whether a run is hidden.

        Direct formatting wins outright. Otherwise vanish is one of Word's toggle
        properties (ECMA-376 17.7.3): a document default of true applies, and
        otherwise the paragraph and character style values are XORed, so hiding in
        both styles shows the text.
        """
        if direct is not None:
            return direct
        if self.default_vanish:
            return True
        paragraph_style = paragraph if paragraph is not None else self.default_paragraph
        return self.resolve(self.paragraph, paragraph_style) != self.resolve(
            self.character, character
        )


def _split_name(name: str) -> tuple[str, str]:
    """Split an expat "namespace local" name."""
    namespace, _, local = name.rpartition(" ")
    return namespace, local


def _word(name: str) -> str | None:
    """Return the local name if this is a WordprocessingML element or attribute."""
    namespace, local = _split_name(name)
    return local if namespace in _W_NAMESPACES else None


def _word_attr(attrs: dict[str, str], local: str) -> str | None:
    for name, value in attrs.items():
        if _word(name) == local:
            return value
    return None


def _toggle(attrs: dict[str, str]) -> bool:
    value = _word_attr(attrs, "val")
    return value is None or value.strip().lower() not in _FALSE_VALUES


class _StylesHandler:
    """Collect style definitions from styles.xml."""

    def __init__(self) -> None:
        self.styles = _Styles()
        self._stack: list[str | None] = []
        self._current: _Style | None = None

    def start(self, name: str, attrs: dict[str, str]) -> None:
        local = _word(name)
        parent = self._stack[-1] if self._stack else None
        grandparent = self._stack[-2] if len(self._stack) > 1 else None
        if local == "style":
            self._start_style(attrs)
        elif local == "basedOn" and parent == "style" and self._current is not None:
            self._current.based_on = _word_attr(attrs, "val")
        elif local == "vanish" and parent == "rPr":
            if grandparent == "style" and self._current is not None:
                self._current.vanish = _toggle(attrs)
            elif grandparent == "rPrDefault":
                self.styles.default_vanish = _toggle(attrs)
        self._stack.append(local)

    def _start_style(self, attrs: dict[str, str]) -> None:
        self._current = None
        style_id = _word_attr(attrs, "styleId")
        style_type = _word_attr(attrs, "type")
        if style_id is None:
            return
        if style_type == "paragraph":
            self._current = self.styles.paragraph.setdefault(style_id, _Style())
            if _toggle_attr_is_set(_word_attr(attrs, "default")):
                self.styles.default_paragraph = style_id
        elif style_type == "character":
            self._current = self.styles.character.setdefault(style_id, _Style())

    def end(self, _name: str) -> None:
        self._stack.pop()


def _toggle_attr_is_set(value: str | None) -> bool:
    return value is not None and value.strip().lower() not in _FALSE_VALUES


@dataclass
class _Run:
    vanish: bool | None = None
    character_style: str | None = None
    has_text: bool = False


class _ContentHandler:
    """Count hidden text runs and images pandoc will drop in one content part."""

    def __init__(self, styles: _Styles, available_targets: frozenset[str]) -> None:
        self._styles = styles
        self._available_targets = available_targets
        self._stack: list[str | None] = []
        self._runs: list[_Run] = []
        # Text boxes nest whole paragraphs inside a run, so both need stacks.
        self._paragraph_styles: list[str | None] = []
        self.hidden_runs = 0
        self.dropped_images = 0

    def start(self, name: str, attrs: dict[str, str]) -> None:
        local = _word(name)
        parent = self._stack[-1] if self._stack else None
        grandparent = self._stack[-2] if len(self._stack) > 1 else None
        if local == "p":
            self._paragraph_styles.append(None)
        elif local == "pStyle" and parent == "pPr" and self._paragraph_styles:
            self._paragraph_styles[-1] = _word_attr(attrs, "val")
        elif local == "r":
            self._runs.append(_Run())
        elif parent == "rPr" and grandparent == "r" and self._runs:
            if local == "vanish":
                self._runs[-1].vanish = _toggle(attrs)
            elif local == "rStyle":
                self._runs[-1].character_style = _word_attr(attrs, "val")
        self._check_image(name, attrs)
        self._stack.append(local)

    def _check_image(self, name: str, attrs: dict[str, str]) -> None:
        namespace, local = _split_name(name)
        if namespace in _A_NAMESPACES and local == "blip":
            embed = self._relationship_attr(attrs, "embed")
            link = self._relationship_attr(attrs, "link")
            if (embed is None and link is not None) or (
                embed is not None and self._is_dropped(embed)
            ):
                self.dropped_images += 1
        elif namespace == _VML_NAMESPACE and local == "imagedata":
            rel_id = self._relationship_attr(attrs, "id")
            if rel_id is not None and self._is_dropped(rel_id):
                self.dropped_images += 1

    def _is_dropped(self, rel_id: str) -> bool:
        return rel_id not in self._available_targets

    @staticmethod
    def _relationship_attr(attrs: dict[str, str], local: str) -> str | None:
        for name, value in attrs.items():
            namespace, attr_local = _split_name(name)
            if namespace in _R_NAMESPACES and attr_local == local:
                return value
        return None

    def characters(self, data: str) -> None:
        if self._stack and self._stack[-1] == "t" and self._runs and data.strip():
            self._runs[-1].has_text = True

    def end(self, name: str) -> None:
        self._stack.pop()
        local = _word(name)
        if local == "p" and self._paragraph_styles:
            self._paragraph_styles.pop()
        elif local == "r" and self._runs:
            run = self._runs.pop()
            paragraph = self._paragraph_styles[-1] if self._paragraph_styles else None
            if run.has_text and self._styles.is_hidden(
                direct=run.vanish, character=run.character_style, paragraph=paragraph
            ):
                self.hidden_runs += 1


class _RelationshipsHandler:
    """Collect entries from a .rels part."""

    def __init__(self) -> None:
        self.relationships: list[_Relationship] = []

    def start(self, name: str, attrs: dict[str, str]) -> None:
        namespace, local = _split_name(name)
        if namespace == _RELS_NAMESPACE and local == "Relationship":
            self.relationships.append(
                _Relationship(
                    rel_id=attrs.get("Id", ""),
                    rel_type=attrs.get("Type", ""),
                    target=attrs.get("Target", ""),
                    external=attrs.get("TargetMode", "").lower() == "external",
                )
            )

    def end(self, _name: str) -> None:
        pass


class _XmlReader:
    """Streaming XML parser that rejects DOCTYPEs and excessive nesting."""

    def __init__(self, archive: zipfile.ZipFile, limits: Limits) -> None:
        self._archive = archive
        self._limits = limits

    def parse(
        self,
        info: zipfile.ZipInfo,
        start: Callable[[str, dict[str, str]], None] | None = None,
        end: Callable[[str], None] | None = None,
        characters: Callable[[str], None] | None = None,
    ) -> None:
        name = sanitize(info.filename)
        parser = expat.ParserCreate(namespace_separator=" ")
        parser.buffer_text = True
        depth = 0

        def on_doctype(*_args: object) -> None:
            raise _RejectedXml("contains a DOCTYPE, which Word documents never use")

        def on_start(tag: str, attrs: dict[str, str]) -> None:
            nonlocal depth
            depth += 1
            if depth > self._limits.max_xml_depth:
                raise ResourceLimitError(
                    f"{name} is nested more than {self._limits.max_xml_depth} levels deep"
                )
            if start is not None:
                start(tag, attrs)

        def on_end(tag: str) -> None:
            nonlocal depth
            depth -= 1
            if end is not None:
                end(tag)

        parser.StartDoctypeDeclHandler = on_doctype
        parser.StartElementHandler = on_start
        parser.EndElementHandler = on_end
        if characters is not None:
            parser.CharacterDataHandler = characters

        # Sizes were already checked against the zip headers. zipfile never reads past the
        # declared size and fails the CRC check, so a lying header can't get around them.
        try:
            with self._archive.open(info) as stream:
                while chunk := stream.read(_CHUNK_SIZE):
                    parser.Parse(chunk, False)
            parser.Parse(b"", True)
        except expat.ExpatError as exc:
            raise InvalidDocumentError(f"{name} is not well-formed XML: {exc}") from exc
        except _RejectedXml as exc:
            raise InvalidDocumentError(f"{name} {exc}") from exc
        except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError) as exc:
            raise InvalidDocumentError(f"could not read {name}: {sanitize(exc)}") from exc


def _check_path(path: Path, limits: Limits) -> None:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {sanitize(path)}")
    if not path.is_file():
        raise InvalidDocumentError(f"Not a regular file: {sanitize(path)}")
    if path.suffix.lower() != ".docx":
        raise InvalidDocumentError(f"Expected a .docx file, got: {sanitize(path.name)}")
    size = path.stat().st_size
    if size == 0:
        raise InvalidDocumentError(f"File is empty: {sanitize(path)}")
    if size > limits.max_file_size:
        raise ResourceLimitError(
            f"File is {format_size(size)}, over the {format_size(limits.max_file_size)} limit"
        )


def _check_header(path: Path) -> None:
    with path.open("rb") as stream:
        if stream.read(len(_OLE2_MAGIC)) == _OLE2_MAGIC:
            raise InvalidDocumentError(
                "This is a password-protected or legacy .doc file, not a .docx. "
                "Remove the password, or re-save it as .docx in Word."
            )


def _check_members(archive: zipfile.ZipFile, limits: Limits) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > limits.max_zip_entries:
        raise ResourceLimitError(
            f"archive has {len(infos)} entries, over the limit of {limits.max_zip_entries}"
        )

    members: dict[str, zipfile.ZipInfo] = {}
    declared_total = 0
    for info in infos:
        name = info.filename
        shown = sanitize(name)
        parts = name.replace("\\", "/").split("/")
        if name.startswith(("/", "\\")) or _DRIVE_PREFIX.match(name) or ".." in parts:
            raise InvalidDocumentError(f"archive member has an unsafe path: {shown}")
        if info.flag_bits & _ENCRYPTED_FLAG:
            raise InvalidDocumentError(f"archive member is encrypted: {shown}")
        if name in members:
            # Our scan and pandoc might read different copies, defeating the scan.
            raise InvalidDocumentError(f"archive has more than one member named {shown}")
        if name.endswith((".xml", ".rels")) and info.file_size > limits.max_xml_part_size:
            raise ResourceLimitError(
                f"{shown} is larger than the "
                f"{format_size(limits.max_xml_part_size)} limit for one part"
            )
        declared_total += info.file_size
        members[name] = info

    if declared_total > limits.max_total_uncompressed:
        raise ResourceLimitError(
            f"document expands to {format_size(declared_total)}, over the "
            f"{format_size(limits.max_total_uncompressed)} limit"
        )
    return members


def _read_relationships(
    reader: _XmlReader, members: dict[str, zipfile.ZipInfo], rels_name: str
) -> list[_Relationship]:
    info = members.get(rels_name)
    if info is None:
        return []
    handler = _RelationshipsHandler()
    reader.parse(info, handler.start, handler.end)
    return handler.relationships


def _resolve_target(base_dir: str, target: str) -> str:
    """Resolve a relationship target to an archive member name."""
    joined = target.lstrip("/") if target.startswith("/") else posixpath.join(base_dir, target)
    return posixpath.normpath(joined)


def _inspect_archive(archive: zipfile.ZipFile, limits: Limits) -> DocxInspection:
    members = _check_members(archive, limits)
    for required in ("[Content_Types].xml", "_rels/.rels"):
        if required not in members:
            raise InvalidDocumentError(f"not a Word document: {required} is missing")

    reader = _XmlReader(archive, limits)
    parsed: set[str] = set()

    def read_rels(rels_name: str) -> list[_Relationship]:
        parsed.add(rels_name)
        return _read_relationships(reader, members, rels_name)

    main_name = next(
        (
            _resolve_target("", rel.target)
            for rel in read_rels("_rels/.rels")
            if rel.rel_type.endswith("/officeDocument") and not rel.external
        ),
        None,
    )
    if main_name is None:
        raise InvalidDocumentError("not a Word document: no main document relationship")
    if main_name not in members:
        raise InvalidDocumentError(
            f"not a Word document: main part {sanitize(main_name)} is missing"
        )

    main_dir, main_file = posixpath.split(main_name)
    main_rels = read_rels(posixpath.join(main_dir, "_rels", f"{main_file}.rels"))

    # Styles must be read first so content parts can resolve them.
    styles_handler = _StylesHandler()
    for rel in main_rels:
        if rel.rel_type.endswith("/styles") and not rel.external:
            name = _resolve_target(main_dir, rel.target)
            if name in members and name not in parsed:
                parsed.add(name)
                reader.parse(members[name], styles_handler.start, styles_handler.end)

    # Only the parts pandoc publishes: the body, footnotes and endnotes.
    content_parts = [main_name]
    for rel in main_rels:
        if rel.rel_type.endswith(("/footnotes", "/endnotes")) and not rel.external:
            content_parts.append(_resolve_target(main_dir, rel.target))

    findings: list[Finding] = []
    # An image is lost if it's stored outside the document or its target is missing.
    available_targets = frozenset(
        rel.rel_id
        for rel in main_rels
        if not rel.external and _resolve_target(main_dir, rel.target) in members
    )
    dropped_images = 0
    for part in content_parts:
        if part not in members or part in parsed:
            continue
        parsed.add(part)
        content = _ContentHandler(styles_handler.styles, available_targets)
        reader.parse(members[part], content.start, content.end, content.characters)
        dropped_images += content.dropped_images
        if content.hidden_runs:
            # Report only where and how much. Echoing the text would leak it to logs.
            findings.append(
                Finding(
                    FindingKind.HIDDEN_TEXT,
                    f"{content.hidden_runs} run(s) of text hidden in Word will be visible "
                    f"in the HTML ({sanitize(part)})",
                )
            )
    if dropped_images:
        findings.append(
            Finding(
                FindingKind.LINKED_IMAGE,
                f"{dropped_images} image(s) are not stored in the document "
                "and will be missing from the HTML",
            )
        )

    # Every remaining XML part must still be safe and well-formed: pandoc silently
    # discards all links if a relationships file doesn't parse, for example.
    for name, info in members.items():
        if name not in parsed and name.endswith((".xml", ".rels")):
            parsed.add(name)
            reader.parse(info)

    has_media = any(not name.endswith((".xml", ".rels", "/")) for name in members)
    return DocxInspection(tuple(findings), has_media)


def inspect_docx(path: Path, limits: Limits | None = None) -> DocxInspection:
    """Validate a .docx file and report content that needs the user's attention.

    Raises:
        FileNotFoundError: The file doesn't exist.
        InvalidDocumentError: It isn't a usable .docx file.
        ResourceLimitError: It exceeds a configured limit.
    """
    limits = limits or Limits()
    _check_path(path, limits)
    _check_header(path)
    try:
        with zipfile.ZipFile(path) as archive:
            return _inspect_archive(archive, limits)
    except (zipfile.BadZipFile, UnicodeDecodeError, NotImplementedError, EOFError) as exc:
        raise InvalidDocumentError(
            f"Not a valid .docx file (it is not a readable zip archive): {sanitize(path.name)}"
        ) from exc
