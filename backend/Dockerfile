FROM python:3.12-slim

WORKDIR /app

# libpq is needed at runtime by psycopg[binary]'s C extension on some platforms;
# installing it explicitly keeps the image working even if a future psycopg
# version drops its bundled binary wheel for this arch.
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --no-cache-dir .

EXPOSE 8000

# Run migrations, then serve. For this slice that's fine to do on every
# container start (see README): migrations are idempotent, and there's no
# multi-replica rollout to coordinate here.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
