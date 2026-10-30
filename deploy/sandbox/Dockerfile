# The image every sandbox command runs in: common languages and tools, no secrets.
# Data (pandas, Excel), documents (Word, PowerPoint, PDF), charts, images and web scraping
# are ready to use, so everyday tasks don't start by installing things.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential nodejs npm jq unzip ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pandas openpyxl matplotlib python-docx python-pptx pypdf reportlab \
        requests beautifulsoup4 lxml pillow tabulate \
    && useradd --uid 10001 --create-home --shell /bin/bash dev
USER dev
ENV HOME=/home/dev PIP_USER=1 PATH=/home/dev/.local/bin:$PATH PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /work
