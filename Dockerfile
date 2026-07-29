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

# Pilot deployment: run the durable PostgreSQL-backed Sheets worker alongside the
# API. Production can split this command into a dedicated Railway worker service
# and add Redis without changing the application contract.
CMD ["sh", "-c", "python scripts/sheet_worker.py & exec uvicorn crm.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
