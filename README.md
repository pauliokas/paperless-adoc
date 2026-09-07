# paperless-adoc

paperless-adoc is a custom parser plugin for [paperless-ngx][paperless], which handles .adoc files.

.adoc files are files signed with a qualified signature. The format is specified by The Office of the Chief Archivist of
Lithuania. The full specification can be found in [e-seimas][adoc-specification].

A .adoc file is a ZIP archive consisting of the main document and various XML files defining its metadata. This parser
unwraps the container, resolves the main document via `META-INF/relations.xml`, and delegates parsing of that document
to whichever built-in paperless-ngx parser handles its media type.

[paperless]: https://docs.paperless-ngx.com/
[adoc-specification]: https://e-seimas.lrs.lt/portal/legalActEditions/lt/TAD/TAIS.352152

## Requirements

paperless-ngx **3.0 or newer**. Version 2.x of this plugin targeted the pre-v3
`document_consumer_declaration` signal, which was removed in v3.

## Installation

paperless-ngx v3 discovers parsers through the `paperless_ngx.parsers` [entry point group][parser-plugins],
so the package must be *installed* — mounting the source directory and setting `PAPERLESS_APPS` no longer works.

Install it into the paperless-ngx environment:

```bash
uv pip install --system git+https://github.com/pauliokas/paperless-adoc@v2.0.0
```

For Docker, either bake it into a derived image:

```dockerfile
FROM ghcr.io/paperless-ngx/paperless-ngx:3.1.3
RUN uv pip install --no-cache --system --no-python-downloads --python-preference system \
      https://github.com/pauliokas/paperless-adoc/archive/refs/tags/v2.0.0.tar.gz
```

(the image ships no `git`, so a tarball URL is used rather than `git+`), or install it at container
start with a [custom initialization script][cont-init] mounted into `/custom-cont-init.d`.

Restart paperless-ngx afterwards. On the first consumed document the log should show:

```
Loaded third-party parser 'ADoC Parser' v2.0.0 by Paulius Kigas (entrypoint: 'adoc').
```

[parser-plugins]: https://docs.paperless-ngx.com/advanced_usage/#parser-plugins
[cont-init]: https://docs.paperless-ngx.com/advanced_usage/#custom-container-initialization

## Development

```bash
uv sync
docker compose up --build
```

`compose.yaml` builds a derived paperless-ngx image with the working tree installed into it.
