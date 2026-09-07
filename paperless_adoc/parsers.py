"""
Paperless-ngx parser for .adoc containers.

.adoc files are Lithuanian qualified-signature documents: a ZIP wrapping a
signed main document plus XML metadata. The full specification is in e-seimas
(https://e-seimas.lrs.lt/portal/legalActEditions/lt/TAD/TAIS.352152).

This parser does not understand any document format itself. It unwraps the
container, then delegates the extracted main document to whichever built-in
parser handles its media type, and republishes that parser's results as its own.

Satisfies paperless.parsers.ParserProtocol (paperless-ngx >= 3.0). Nothing from
Django or paperless-ngx is imported at module scope: the registry imports this
module from inside ParserRegistry.discover() while holding a non-reentrant lock,
so touching the registry (or Django settings) at import time would deadlock the
worker permanently.
"""

from __future__ import annotations

import datetime
import logging
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Self
from zipfile import ZipFile

from documents.parsers import ParseError

from paperless_adoc.container import AdocContainerError
from paperless_adoc.container import CONTAINER_MIMETYPE
from paperless_adoc.container import is_adoc_container
from paperless_adoc.container import resolve_main_member
from paperless_adoc.container import resolve_media_type
from paperless_adoc.container import safe_member_filename
from paperless_adoc.container import validate

if TYPE_CHECKING:
    from types import TracebackType

    from paperless.parsers import MetadataEntry
    from paperless.parsers import ParserContext

logger = logging.getLogger("paperless.parsing.adoc")

# libmagic reports these containers as application/zip (it only special-cases
# zips whose "mimetype" member holds a media type it recognises, such as ODF or
# EPUB). The vendor type is declared too so the parser keeps working if a future
# libmagic learns to identify .adoc directly.
_SUPPORTED_MIME_TYPES: dict[str, str] = {
    "application/zip": ".adoc",
    CONTAINER_MIMETYPE: ".adoc",
}

# Namespace used for the container facts we surface in extract_metadata().
NS_ADOC_METADATA = "http://www.archyvai.lt/adoc/2008"

# Beats the built-in parsers, which all score 10.
_SCORE_CONFIRMED = 15
# Returned when there is no file to inspect. Positive, because declining here
# would make is_mime_type_supported("application/zip") false and reject every
# .adoc upload before consumption ever runs; low, because it is a guess.
_SCORE_UNVERIFIED = 5


class AdocParseError(ParseError):
    def __init__(self, message: str) -> None:
        super().__init__(f".adoc parsing error: {message}")


class AdocDocumentParser:
    """Unwrap an .adoc container and delegate its main document to another parser."""

    name: str = "ADoC Parser"
    version: str = "2.0.0"
    author: str = "Paulius Kigas"
    url: str = "https://github.com/pauliokas/paperless-adoc"

    # ------------------------------------------------------------------
    # Class methods
    # ------------------------------------------------------------------

    @classmethod
    def supported_mime_types(cls) -> dict[str, str]:
        return _SUPPORTED_MIME_TYPES

    @classmethod
    def score(
        cls,
        mime_type: str,
        filename: str,
        path: Path | None = None,
    ) -> int | None:
        """Claim the file only when it really is an .adoc container.

        The registry calls this without a try/except, and external parsers are
        evaluated before built-ins, so an exception escaping here would abort
        parser lookup for every document — PDFs included. Everything is
        therefore swallowed into "decline".
        """
        try:
            if mime_type not in _SUPPORTED_MIME_TYPES:
                return None

            if mime_type == CONTAINER_MIMETYPE:
                return _SCORE_CONFIRMED

            if path is None:
                # Pre-flight gate (is_mime_type_supported, mail attachments):
                # there is no file to inspect yet. The real decision happens
                # when the consumer calls again with the working copy.
                return _SCORE_UNVERIFIED

            return _SCORE_CONFIRMED if is_adoc_container(Path(path)) else None
        except Exception:
            logger.warning("Error while scoring %s, declining", filename, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def can_produce_archive(self) -> bool:
        return True

    @property
    def requires_pdf_rendition(self) -> bool:
        # A browser cannot render an .adoc container, so a PDF must always be
        # stored for the document to be viewable at all.
        return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(self, logging_group: object | None = None) -> None:
        from django.conf import settings

        settings.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
        self._tempdir = Path(
            tempfile.mkdtemp(prefix="paperless-adoc-", dir=settings.SCRATCH_DIR),
        )
        self._context: ParserContext | None = None
        self._text: str | None = None
        self._date: datetime.datetime | None = None
        self._archive_path: Path | None = None
        self._thumbnail_path: Path | None = None
        self._page_count: int | None = None
        # Cached result of _ensure_extracted().
        self._inner: tuple[Path, str] | None = None
        self.log = logger

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        logger.debug("Cleaning up temporary directory %s", self._tempdir)
        shutil.rmtree(self._tempdir, ignore_errors=True)

    def configure(self, context: ParserContext) -> None:
        self._context = context

    # ------------------------------------------------------------------
    # Container unwrapping
    # ------------------------------------------------------------------

    def _ensure_extracted(self, document_path: Path) -> tuple[Path, str]:
        """Extract the main document, returning its path and media type.

        Idempotent and cached: get_thumbnail(), get_page_count() and
        extract_metadata() may each be called without a preceding parse()
        (document_thumbnails and the metadata endpoint both do exactly that).

        The extracted file is written into *our* tempdir, so it outlives the
        delegate parser's context manager.
        """
        if self._inner is not None:
            return self._inner

        try:
            with ZipFile(document_path, "r") as archive:
                validate(archive)
                main_member = resolve_main_member(archive)
                media_type = resolve_media_type(archive, main_member)
                payload = archive.read(main_member)
        except AdocContainerError as e:
            raise AdocParseError(str(e)) from e
        except Exception as e:
            raise AdocParseError(f"unreadable .adoc container: {e}") from e

        extracted_path = self._tempdir / safe_member_filename(main_member)
        extracted_path.write_bytes(payload)

        logger.info(
            "Extracted main document %s (%s) from .adoc container",
            main_member,
            media_type,
        )

        self._inner = (extracted_path, media_type)
        return self._inner

    def _resolve_inner_parser(self, inner_path: Path, inner_mime: str):
        """Return the parser class for the extracted document."""
        # Imported lazily: the registry holds a non-reentrant lock across
        # discover() -> ep.load(), which imports this module.
        from paperless.parsers.registry import get_parser_registry

        inner_cls = get_parser_registry().get_parser_for_file(
            inner_mime,
            inner_path.name,
            inner_path,
            # ParserContext carries only mailrule_id, so the consumer's
            # allow_remote decision cannot be recovered here. Refuse remote
            # parsers rather than silently shipping document content offsite:
            # RemoteDocumentParser scores 20 and would outrank Tesseract.
            allow_remote=False,
        )

        if inner_cls is None:
            raise AdocParseError(
                f"no parser found for the main document's media type: {inner_mime}",
            )

        # Guard against re-entering ourselves, which would recurse until the
        # scratch volume filled up. Matched on identity, mime and name so a
        # duplicate import of this module cannot slip through.
        if (
            inner_cls is type(self)
            or inner_mime in _SUPPORTED_MIME_TYPES
            or getattr(inner_cls, "name", None) == self.name
        ):
            raise AdocParseError(
                f"refusing to delegate {inner_mime} back to the .adoc parser",
            )

        return inner_cls

    # ------------------------------------------------------------------
    # Core parsing interface
    # ------------------------------------------------------------------

    def parse(
        self,
        document_path: Path,
        mime_type: str,
        *,
        produce_archive: bool = True,
    ) -> None:
        from documents.consumer import should_produce_archive

        inner_path, inner_mime = self._ensure_extracted(Path(document_path))
        inner_cls = self._resolve_inner_parser(inner_path, inner_mime)

        # Everything the delegate produces lands in the delegate's own tempdir,
        # which its __exit__ deletes. Capture it all before leaving this block.
        with inner_cls() as inner:
            if self._context is not None:
                inner.configure(self._context)

            logger.info(
                "Delegating %s to %s v%s",
                inner_mime,
                getattr(inner, "name", inner_cls.__name__),
                getattr(inner, "version", "unknown"),
            )

            # Decide for the inner document rather than forwarding our own flag:
            # requires_pdf_rendition makes should_produce_archive() return True
            # unconditionally for us, which would force a full OCR pass over
            # PDFs that already carry text.
            inner_produce_archive = should_produce_archive(
                inner,
                inner_mime,
                inner_path,
                logger,
            )

            inner.parse(inner_path, inner_mime, produce_archive=inner_produce_archive)

            self._text = inner.get_text()
            self._date = inner.get_date()
            self._page_count = inner.get_page_count(inner_path, inner_mime)

            inner_archive = inner.get_archive_path()
            if inner_archive is not None:
                self._archive_path = self._tempdir / "archive.pdf"
                shutil.copy2(inner_archive, self._archive_path)

            self._capture_thumbnail(inner, inner_path, inner_mime)

        if self._archive_path is None:
            self._adopt_inner_as_archive(inner_path, inner_mime)

    def _capture_thumbnail(self, inner, inner_path: Path, inner_mime: str) -> None:
        """Copy the delegate's thumbnail into our tempdir.

        Delegating beats regenerating from the archive: it also yields correct
        thumbnails for image, text and Tika-handled inner documents.
        """
        try:
            generated = inner.get_thumbnail(inner_path, inner_mime)
        except Exception:
            logger.warning(
                "Delegate could not generate a thumbnail, will fall back",
                exc_info=True,
            )
            return

        if generated is None:
            return

        thumbnail_path = self._tempdir / f"thumbnail{Path(generated).suffix or '.webp'}"
        shutil.copy2(generated, thumbnail_path)
        self._thumbnail_path = thumbnail_path

    def _adopt_inner_as_archive(self, inner_path: Path, inner_mime: str) -> None:
        """Use the extracted document itself as the archive when it is a PDF.

        requires_pdf_rendition is a promise the consumer does not verify — it
        only checks `if archive_path and is_file()`, so returning nothing here
        would silently store a document with no viewable rendition. A
        born-digital PDF needs no conversion, so adopt it directly; anything
        else is a hard failure.
        """
        if inner_mime == "application/pdf":
            self._archive_path = self._tempdir / "archive.pdf"
            shutil.copy2(inner_path, self._archive_path)
            logger.info("Adopted the extracted PDF as the archive version")
            return

        raise AdocParseError(
            f"could not produce a PDF rendition for main document type {inner_mime}",
        )

    # ------------------------------------------------------------------
    # Result accessors
    # ------------------------------------------------------------------

    def get_text(self) -> str:
        return self._text or ""

    def get_date(self) -> datetime.datetime | None:
        return self._date

    def get_archive_path(self) -> Path | None:
        return self._archive_path

    # ------------------------------------------------------------------
    # Thumbnail, page count and metadata
    # ------------------------------------------------------------------

    def get_thumbnail(self, document_path: Path, mime_type: str) -> Path:
        """Return a thumbnail, generating one if parse() has not run.

        Callers may move the returned file (document_thumbnails does), so this
        must never hand back a shared asset. make_thumbnail_from_pdf() already
        copies the default thumbnail into temp_dir on failure, so routing
        through it is safe.
        """
        if self._thumbnail_path is not None:
            return self._thumbnail_path

        from documents.parsers import make_thumbnail_from_pdf

        source = self._archive_path
        if source is None:
            inner_path, inner_mime = self._ensure_extracted(Path(document_path))
            if inner_mime != "application/pdf":
                raise AdocParseError(
                    f"cannot generate a thumbnail for main document type {inner_mime}"
                    " without parsing it first",
                )
            source = inner_path

        return make_thumbnail_from_pdf(source, self._tempdir)

    def get_page_count(self, document_path: Path, mime_type: str) -> int | None:
        """Return the page count of the derived PDF.

        *document_path* is the .adoc container, so it is never passed to a PDF
        helper directly.
        """
        if self._page_count is not None:
            return self._page_count

        from paperless.parsers.utils import get_page_count_for_pdf

        source = self._archive_path
        if source is None:
            inner_path, inner_mime = self._ensure_extracted(Path(document_path))
            if inner_mime != "application/pdf":
                return None
            source = inner_path

        return get_page_count_for_pdf(source, log=logger)

    def extract_metadata(
        self,
        document_path: Path,
        mime_type: str,
    ) -> list[MetadataEntry]:
        """Return metadata of the embedded document, plus container facts.

        Called without a preceding parse() by the metadata endpoint, and
        contractually must not raise.
        """
        entries: list[MetadataEntry] = []

        try:
            inner_path, inner_mime = self._ensure_extracted(Path(document_path))
        except Exception:
            logger.warning(
                "Could not read .adoc container metadata for %s",
                document_path,
                exc_info=True,
            )
            return entries

        entries.append(
            {
                "namespace": NS_ADOC_METADATA,
                "prefix": "adoc",
                "key": "MainDocumentMediaType",
                "value": inner_mime,
            },
        )
        entries.append(
            {
                "namespace": NS_ADOC_METADATA,
                "prefix": "adoc",
                "key": "MainDocumentName",
                "value": inner_path.name,
            },
        )

        if inner_mime == "application/pdf":
            from paperless.parsers.utils import extract_pdf_metadata

            entries.extend(extract_pdf_metadata(inner_path, log=logger))

        return entries

