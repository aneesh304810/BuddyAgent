# The image every sandbox command runs in: common languages and tools, no secrets.
# Data (pandas, Excel), documents (Word, PowerPoint, PDF), charts, images and web scraping
# are ready to use, so everyday tasks don't start by installing things.
# pip and npm go through Buddy's install guard (/buddy/guard/pkgguard.py, mounted read-only):
# registry tools at their pinned versions, other packages only if established and not lookalike
# names. install-tool installs from the registry. (python -m pip and npx aren't guarded: the
# sandbox's isolation is the safety net for those.)
# Debian 12 (bookworm): Playwright's browser installer supports it (Debian 13 "trixie" isn't supported yet and the
# build fails looking for Ubuntu font packages).
FROM python:3.12-slim-bookworm
# A headless Chromium (Playwright) for Buddy's browser (browser/driver.py), shared by every user of the image.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential nodejs npm jq unzip ripgrep graphviz \
    && pip install --no-cache-dir pandas openpyxl matplotlib python-docx python-pptx pypdf reportlab \
        requests beautifulsoup4 lxml pillow tabulate httpx pyyaml tzdata playwright==1.49.1 \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /opt/pw-browsers \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /bin/bash dev \
    && for p in pip pip3 pip3.12; do [ -e /usr/local/bin/$p ] && mv /usr/local/bin/$p /usr/local/bin/$p-unguarded || true; done \
    && mv /usr/bin/npm /usr/bin/npm-real \
    && printf '#!/bin/sh\nexec python3 -m pip "$@"\n' > /usr/local/bin/pip-real \
    && printf '#!/bin/sh\nif [ -f /buddy/guard/pkgguard.py ]; then exec python3 /buddy/guard/pkgguard.py pip "$@"; fi\nexec pip-real "$@"\n' > /usr/local/bin/pip \
    && printf '#!/bin/sh\nif [ -f /buddy/guard/pkgguard.py ]; then exec python3 /buddy/guard/pkgguard.py npm "$@"; fi\nexec npm-real "$@"\n' > /usr/local/bin/npm \
    && printf '#!/bin/sh\nexec python3 /buddy/guard/install_tool.py "$@"\n' > /usr/local/bin/install-tool \
    && chmod 755 /usr/local/bin/pip /usr/local/bin/pip-real /usr/local/bin/npm /usr/local/bin/install-tool \
    && ln -sf /usr/local/bin/pip /usr/local/bin/pip3
COPY browser/driver.py /opt/buddy-browser/driver.py
USER dev
ENV HOME=/home/dev PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /work
