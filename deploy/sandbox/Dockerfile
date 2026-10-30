# The image every sandbox command runs in: common languages and tools, no secrets.
# Data (pandas, Excel), documents (Word, PowerPoint, PDF), charts, images and web scraping
# are ready to use, so everyday tasks don't start by installing things.
# pip and npm go through Buddy's install guard (/buddy/guard/pkgguard.py, mounted read-only):
# registry tools at their pinned versions, other packages only if established and not lookalike
# names. install-tool installs from the registry. (python -m pip and npx aren't guarded: the
# sandbox's isolation is the safety net for those.)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential nodejs npm jq unzip ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pandas openpyxl matplotlib python-docx python-pptx pypdf reportlab \
        requests beautifulsoup4 lxml pillow tabulate httpx pyyaml \
    && useradd --uid 10001 --create-home --shell /bin/bash dev \
    && for p in pip pip3 pip3.12; do [ -e /usr/local/bin/$p ] && mv /usr/local/bin/$p /usr/local/bin/$p-unguarded || true; done \
    && mv /usr/bin/npm /usr/bin/npm-real \
    && printf '#!/bin/sh\nexec python3 -m pip "$@"\n' > /usr/local/bin/pip-real \
    && printf '#!/bin/sh\nif [ -f /buddy/guard/pkgguard.py ]; then exec python3 /buddy/guard/pkgguard.py pip "$@"; fi\nexec pip-real "$@"\n' > /usr/local/bin/pip \
    && printf '#!/bin/sh\nif [ -f /buddy/guard/pkgguard.py ]; then exec python3 /buddy/guard/pkgguard.py npm "$@"; fi\nexec npm-real "$@"\n' > /usr/local/bin/npm \
    && printf '#!/bin/sh\nexec python3 /buddy/guard/install_tool.py "$@"\n' > /usr/local/bin/install-tool \
    && chmod 755 /usr/local/bin/pip /usr/local/bin/pip-real /usr/local/bin/npm /usr/local/bin/install-tool \
    && ln -sf /usr/local/bin/pip /usr/local/bin/pip3
USER dev
ENV HOME=/home/dev PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /work
