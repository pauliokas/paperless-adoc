import xml.etree.ElementTree as ET
from zipfile import ZipFile

from documents.parsers import DocumentParser, make_thumbnail_from_pdf, get_parser_class_for_mime_type, ParseError

NS_ADOC = "http://www.archyvai.lt/adoc/2008/relationships"
NS_MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"


class AdocParseError(ParseError):
    def __init__(self, message):
        super().__init__(f".adoc parsing error: {message}")


class AdocDocumentParser(DocumentParser):

    logging_name = "paperless.parsing.adoc"

    def parse(self, document_path, mime_type, file_name=None):
        inner_document_path, inner_document_mimetype = self.extract_pdf(document_path, file_name)

        self.log.info("Discovered main document %s with MIME type %s inside .adoc archive", inner_document_path, inner_document_mimetype)

        pdf_parser_class = get_parser_class_for_mime_type(inner_document_mimetype)
        if pdf_parser_class is None:
            raise AdocParseError(f"no parser found for inner document's MIME type: {inner_document_mimetype}")
        pdf_parser = pdf_parser_class(self.logging_group, self.progress_callback)
        pdf_parser.parse(inner_document_path, inner_document_mimetype)

        self.archive_path = pdf_parser.archive_path
        self.text = pdf_parser.text
        self.date = pdf_parser.date

    def get_thumbnail(self, document_path, mime_type, file_name=None):
        return make_thumbnail_from_pdf(self.archive_path, self.tempdir, self.logging_group)

    def extract_pdf(self, document_path, file_name):
        with ZipFile(document_path, "r") as adoc_archive:
            main_document_path = _resolve_main_filename(adoc_archive)
            mimetype = _resolve_main_mimetype(adoc_archive, main_document_path)

            extracted_path = self.tempdir / main_document_path
            extracted_path.write_bytes(adoc_archive.read(main_document_path))

            return extracted_path, mimetype

    def get_settings(self):
        return {}


def _resolve_main_mimetype(adoc_archive: ZipFile, main_document_path: str) -> str:
    if adoc_archive.getinfo("META-INF/manifest.xml") is None:
        raise AdocParseError("missing META-INF/manifest.xml")

    manifest_xml = ET.parse(adoc_archive.open("META-INF/manifest.xml"))

    mimetype_element = manifest_xml.find(f".//manifest:file-entry[@manifest:full-path='{main_document_path}']", {"manifest": NS_MANIFEST})

    mimetype = mimetype_element.attrib.get(f"{{{NS_MANIFEST}}}media-type")

    if mimetype is None:
        raise AdocParseError("missing media-type for main document in manifest.xml")

    return mimetype


def _resolve_main_filename(adoc_archive: ZipFile) -> str:
    if adoc_archive.getinfo("META-INF/relations.xml") is None:
        raise AdocParseError("missing META-INF/relations.xml")

    relations_xml = ET.parse(adoc_archive.open("META-INF/relations.xml"))

    main_elements = list(relations_xml.findall(".//adoc:Relationship[@type='http://www.archyvai.lt/adoc/2008/relationships/content/main']", {"adoc": NS_ADOC}))
    if len(main_elements) != 1:
        raise AdocParseError("missing or multiple main content relationships")

    main_document_path = main_elements[0].attrib.get("full-path")

    if adoc_archive.getinfo(main_document_path) is None:
        raise AdocParseError(f"missing main document at {main_document_path}")

    return main_document_path
