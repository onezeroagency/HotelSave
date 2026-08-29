# MyRoomWatch backend — works on any container host (Railway, Fly, etc.).
# The same image serves both processes:
#   API:       docker run <img>                       (default CMD below)
#   Scheduler: docker run <img> python -m app.scheduler.worker --loop
# Run `alembic upgrade head` once per deploy (release phase / one-off command).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

# Install deps first for layer caching.
COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
