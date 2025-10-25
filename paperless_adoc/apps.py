from django.apps import AppConfig

from paperless_adoc.signals import adoc_consumer_declaration


class PaperlessAdocConfig(AppConfig):
    name = "paperless_adoc"

    def ready(self):
        from documents.signals import document_consumer_declaration

        document_consumer_declaration.connect(adoc_consumer_declaration)

        AppConfig.ready(self)
