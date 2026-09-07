"""
Reading of .adoc containers.

An .adoc file is a ZIP archive specified by the Office of the Chief Archivist of
Lithuania. It carries a signed main document plus XML metadata:

    mimetype                  literal "application/vnd.lt.archyvai.adoc-2008"
    META-INF/relations.xml    declares which member is the main content
    META-INF/manifest.xml     declares each member's media type

Nothing in this module imports Django or paperless-ngx, so it is safe to import
at module scope and can be unit-tested standalone.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import BadZipFile
from zipfile import ZipFile

NS_ADOC = "http://www.archyvai.lt/adoc/2008/relationships"
NS_MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"

CONTAINER_MIMETYPE = "application/vnd.lt.archyvai.adoc-2008"
REL_MAIN = "http://www.archyvai.lt/adoc/2008/relationships/content/main"

_MIMETYPE_MEMBER = "mimetype"
_RELATIONS_MEMBER = "META-INF/relations.xml"
_MANIFEST_MEMBER = "META-INF/manifest.xml"

# The mimetype member is a single short media-type string. Anything larger is
# malformed (or a decompression bomb) and is not worth reading.
_MAX_MIMETYPE_SIZE = 1024


class AdocContainerError(Exception):
    """Raised when a file is not a well-formed .adoc container."""


def is_adoc_container(path: Path) -> bool:
    """Return True if *path* is a ZIP whose ``mimetype`` member marks it as .adoc.

    Never raises. Used from ``score()``, where any exception would abort parser
    lookup for every document, so all failures are reported as "not an .adoc".
    """
    try:
        with ZipFile(path, "r") as archive:
            return _read_container_mimetype(archive) == CONTAINER_MIMETYPE
    except (OSError, BadZipFile, KeyError, AdocContainerError, ValueError):
        return False


def _read_container_mimetype(archive: ZipFile) -> str:
    info = _require_member(archive, _MIMETYPE_MEMBER)
    if info.file_size > _MAX_MIMETYPE_SIZE:
        raise AdocContainerError(
            f"{_MIMETYPE_MEMBER} member is implausibly large ({info.file_size} bytes)",
        )
    return archive.read(_MIMETYPE_MEMBER).decode("utf-8", errors="replace").strip()


def _require_member(archive: ZipFile, name: str):
    """Return the member's ZipInfo, raising AdocContainerError when absent.

    ``ZipFile.getinfo()`` raises ``KeyError`` for a missing member rather than
    returning ``None``, so it cannot be probed with a ``is None`` check.
    """
    try:
        return archive.getinfo(name)
    except KeyError:
        raise AdocContainerError(f"missing {name}") from None


def validate(archive: ZipFile) -> None:
    """Check that the container is an .adoc and carries the metadata we need."""
    container_mimetype = _read_container_mimetype(archive)
    if container_mimetype != CONTAINER_MIMETYPE:
        raise AdocContainerError(
            f"not an .adoc container, invalid mimetype: {container_mimetype}",
        )

    _require_member(archive, _RELATIONS_MEMBER)
    _require_member(archive, _MANIFEST_MEMBER)


def resolve_main_member(archive: ZipFile) -> str:
    """Return the archive member holding the main (signed) document."""
    relations = ET.parse(archive.open(_RELATIONS_MEMBER))

    main_paths = [
        full_path
        for element in relations.iter(f"{{{NS_ADOC}}}Relationship")
        if element.attrib.get("type") == REL_MAIN
        and (full_path := element.attrib.get("full-path"))
    ]

    if len(main_paths) != 1:
        raise AdocContainerError(
            f"expected exactly one main content relationship, found {len(main_paths)}",
        )

    main_path = main_paths[0]
    _require_member(archive, main_path)
    return main_path


def resolve_media_type(archive: ZipFile, member: str) -> str:
    """Return the manifest-declared media type for *member*.

    Attributes are compared in Python rather than via an XPath predicate:
    ElementTree's XPath subset cannot express a quoted value, so interpolating
    an untrusted ``full-path`` (one containing an apostrophe) raises
    ``SyntaxError: invalid predicate``.
    """
    manifest = ET.parse(archive.open(_MANIFEST_MEMBER))

    for element in manifest.iter(f"{{{NS_MANIFEST}}}file-entry"):
        if element.attrib.get(f"{{{NS_MANIFEST}}}full-path") != member:
            continue
        media_type = element.attrib.get(f"{{{NS_MANIFEST}}}media-type")
        if media_type:
            return media_type
        raise AdocContainerError(f"missing media-type for {member} in manifest.xml")

    raise AdocContainerError(f"no manifest entry for {member}")


def safe_member_filename(member: str, fallback: str = "document") -> str:
    """Return a flat, safe filename for an archive member.

    Manifest paths may contain directory components (``Path.write_bytes`` would
    raise ``FileNotFoundError``) or traversal segments such as ``../`` (zip
    slip), so only the final component is kept. Degenerate values like ``""``,
    ``"."`` and ``".."`` collapse to an empty name and fall back.
    """
    name = Path(member).name
    if not name or name in {".", ".."}:
        return fallback
    return name
