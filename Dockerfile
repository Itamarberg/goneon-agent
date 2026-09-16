# One container for the API. Data layers are baked in (see docs/PLAN.md §5), so a
# running instance never depends on a third-party WFS at request time.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir . && pip install --no-cache-dir ".[agent]"

COPY domain/ ./domain/
COPY checks/ ./checks/
COPY catalog/ ./catalog/
COPY data/ ./data/
COPY generate/ ./generate/
COPY tools/ ./tools/
COPY agent/ ./agent/
COPY api/ ./api/

EXPOSE 8000
# Render supplies $PORT.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
