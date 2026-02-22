# backend/Dockerfile
FROM python:3.11-slim-bookworm

# Install system deps: ffmpeg for audio processing, curl for healthcheck
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (Docker layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY ./app /app/app

# Pre-create directories with correct ownership before dropping privileges
RUN mkdir -p /downloads /app/data/cookies && \
    useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app /downloads

USER appuser

EXPOSE 8000

# Use exec form so SIGTERM propagates correctly for graceful shutdown
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]