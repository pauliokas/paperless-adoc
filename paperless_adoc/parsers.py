from zipfile import ZipFile

from documents.parsers import DocumentParser, make_thumbnail_from_pdf, get_parser_class_for_mime_type, ParseError


class AdocDocumentParser(DocumentParser):

    logging_name = "paperless.parsing.adoc"

    def parse(self, document_path, mime_type, file_name=None):
        self.archive_path = self.extract_pdf(document_path, file_name)

        pdf_parser_class = get_parser_class_for_mime_type("application/pdf")
        pdf_parser = pdf_parser_class(self.logging_group, self.progress_callback)
        pdf_parser.parse(self.archive_path, "application/pdf")
        self.text = pdf_parser.text
        self.date = pdf_parser.date

    def get_thumbnail(self, document_path, mime_type, file_name=None):
        return make_thumbnail_from_pdf(self.archive_path, self.tempdir, self.logging_group)

    def extract_pdf(self, document_path, file_name):
        with ZipFile(document_path, "r") as adoc_archive:
            pdf_file = next((n for n in adoc_archive.namelist() if n.lower().endswith(".pdf")), None)
            if not pdf_file:
                raise ParseError("No PDF found inside .adoc archive")

            extracted_path = self.tempdir / f"{file_name[0:file_name.rfind('.')]}.pdf"
            extracted_path.write_bytes(adoc_archive.read(pdf_file))

            return extracted_path

    def get_settings(self):
        return {}
