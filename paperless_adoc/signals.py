def adoc_consumer_declaration(sender, **kwargs):
    from paperless_adoc.parsers import AdocDocumentParser
    return {
        "parser": AdocDocumentParser,
        "weight": 0,
        "mime_types": {
            "application/zip": ".adoc",
        },
    }
