#!/bin/sh
# Refresh yt-dlp (nightly channel) + PO-token plugin on every start.
# The venv is a named volume, so the upgraded version survives restarts and
# the image is never the source of truth for yt-dlp's version.
VENV=/opt/engine-venv
echo "[entrypoint] yt-dlp before update: $($VENV/bin/python -c 'import yt_dlp; print(yt_dlp.version.__version__)' 2>/dev/null || echo none)"
timeout 120 $VENV/bin/pip install -q -U --pre "yt-dlp[default]" bgutil-ytdlp-pot-provider \
    || echo "[entrypoint] yt-dlp update skipped (offline or timed out) - continuing with current version"
echo "[entrypoint] yt-dlp running: $($VENV/bin/python -c 'import yt_dlp; print(yt_dlp.version.__version__)')"

exec $VENV/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
