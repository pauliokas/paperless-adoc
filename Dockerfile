# Local development image: paperless-ngx with the working tree installed.
#
# The plugin must be installed (not mounted) so that its "paperless_ngx.parsers"
# entry point is present in the distribution metadata the registry scans.
FROM ghcr.io/paperless-ngx/paperless-ngx:3.1.3

COPY pyproject.toml README.md /tmp/paperless-adoc/
COPY paperless_adoc /tmp/paperless-adoc/paperless_adoc

# Mirrors upstream's own flags (see paperless-ngx Dockerfile): --system installs
# into the image's system Python, and the two --*python* flags stop uv from
# fetching its own CPython and installing where Django would never find it.
RUN uv pip install --no-cache --system --no-python-downloads --python-preference system \
      /tmp/paperless-adoc \
    && rm -rf /tmp/paperless-adoc

