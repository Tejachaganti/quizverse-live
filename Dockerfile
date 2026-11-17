# Production Dockerfile for QuizVerse Live
# Python 3.13 Debian-slim base with non-root security context

FROM python:3.13-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    HOST=0.0.0.0 \
    ENVIRONMENT=production

WORKDIR /app

# Install minimal OS dependencies for healthchecks and networking
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install build & runtime dependencies
COPY pyproject.toml alembic.ini ./
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir ".[postgres]"

# Copy application source code and migrations
COPY quizverse ./quizverse

# Create unprivileged application user
RUN useradd -m -u 1001 appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Healthcheck targeting the readiness probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:${PORT}/ready || exit 1

# Production single-process entrypoint with WebSocket support
CMD ["python", "-m", "quizverse"]
