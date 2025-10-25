# paperless-adoc

paperless-adoc is a custom parser for [paperless-ngx][paperless], which handles .adoc files.

.adoc files are files signed with a qualified signature. The format is specified by The Office of the Chief Archivist of
Lithuania. The full specification can be found in [e-seimas][adoc-specification].

.adoc file is a ZIP archive consisting of the main document and various XML files defining its metadata. This parser
tries a naive implementation—it picks the first available PDF document in the archive and passes the parsing to the
standard paperless-ngx toolchain.

[paperless]: https://docs.paperless-ngx.com/
[adoc-specification]: https://e-seimas.lrs.lt/portal/legalActEditions/lt/TAD/TAIS.352152

## Usage

To use this parser, mount the [paperless_adoc/](./paperless_adoc) directory to the paperless-ngx container and specify
the [`PAPERLESS_APPS`][paperless-apps-docs] environment variable:

```yaml
services:
  webserver:
    image: ghcr.io/paperless-ngx/paperless-ngx:latest
    # ...
    volumes:
      # - ...
      - ./paperless_adoc:/usr/src/paperless/src/paperless_adoc
    environment:
      # ...
      PAPERLESS_APPS: paperless_adoc # comma separated list of modules
```

[paperless-apps-docs]: https://docs.paperless-ngx.com/configuration/#PAPERLESS_APPS
