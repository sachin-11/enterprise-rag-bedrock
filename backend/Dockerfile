FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir poetry

COPY pyproject.toml poetry.lock* ./
RUN poetry config virtualenvs.create false \
    && poetry install --no-root --no-interaction --no-ansi

COPY . .

EXPOSE 8000

# Render (and most PaaS hosts) inject $PORT at runtime and expect the app to
# bind to it — the 8000 fallback keeps `docker run` working the same way
# locally with no env var set. `exec` hands PID 1 to uvicorn itself (instead
# of leaving the shell as PID 1) so SIGTERM on deploy/scale-down reaches it
# directly for a clean shutdown, not a forced kill after a timeout.
# No --reload: that's a dev-only file-watcher, wrong for a built image.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
