# Injury Prediction API — production image
# Build context is the REPO ROOT (see docker-compose.yml), because the app
# resolves ml/ and web/ as siblings of backend/ at runtime.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Install Python deps first for better layer caching
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --upgrade pip && pip install -r backend/requirements.txt

# Install the Chromium browser used by the heatmap service (+ its OS deps)
RUN playwright install --with-deps chromium

# Copy the application and the sibling directories it depends on at runtime
COPY backend/ backend/
COPY ml/ ml/
COPY web/ web/
# Data files (squads, fixtures, ratings) read by the services + seed scripts.
# The bulky daily_metrics.json is excluded via .dockerignore (CSV suffices to seed).
COPY scripts/ scripts/
COPY data/ data/

WORKDIR /app/backend

EXPOSE 8000
# 2 workers fits a 2GB VPS alongside Postgres + Chromium. Raise if you add RAM.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
