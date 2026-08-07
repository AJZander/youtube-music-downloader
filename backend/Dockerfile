FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DENO_INSTALL=/usr/local

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*

# Deno: mandatory external JS runtime for yt-dlp's YouTube extractor (EJS).
RUN curl -fsSL https://deno.land/install.sh | sh -s -- --yes \
    && deno --version

# App python env lives in a dedicated venv that is mounted as a named volume
# at runtime so yt-dlp self-updates survive container recreation.
COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/engine-venv \
    && /opt/engine-venv/bin/pip install -U pip \
    && /opt/engine-venv/bin/pip install -r /tmp/requirements.txt

RUN useradd -u 1000 -m appuser \
    && mkdir -p /app/data /staging /library \
    && chown -R appuser:appuser /app /staging /opt/engine-venv

WORKDIR /app
COPY --chown=appuser:appuser app /app/app
COPY --chown=appuser:appuser entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

USER appuser
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
