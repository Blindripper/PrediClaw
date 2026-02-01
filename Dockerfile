FROM python:3.11-slim AS base

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ src/
COPY app.py .
COPY skill.md .

ENV PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PREDICLAW_DATA_DIR=/app/data \
    PREDICLAW_DB_PATH=/app/data/prediclaw.db \
    PREDICLAW_ENV=production

RUN mkdir -p /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://localhost:8000/healthz'); r.raise_for_status()"

# Run with a non-root user
RUN adduser --disabled-password --no-create-home appuser && \
    chown -R appuser:appuser /app/data
USER appuser

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
