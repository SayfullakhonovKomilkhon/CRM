FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
COPY scripts ./scripts

ENV PYTHONPATH=/app/src

CMD ["sh", "-c", "uvicorn crm.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
