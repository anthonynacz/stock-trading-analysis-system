#!/bin/sh
# Container entrypoint.
# Runs Alembic migrations to head before starting uvicorn so the app never
# boots against a stale schema. `alembic upgrade head` is idempotent — it's a
# no-op when the DB is already at head, so this is safe on every restart.
#
# Why a shell entrypoint vs invoking from Python lifespan:
#   - alembic's CLI uses `asyncio.run()` inside env.py, which can't be called
#     from inside an already-running event loop (FastAPI's lifespan).
#   - Running it before the Python process starts keeps the contract simple:
#     migrations finish, then the server boots.
set -e

echo "[entrypoint] alembic upgrade head"
alembic upgrade head

echo "[entrypoint] starting uvicorn"
exec uvicorn main:app --host 0.0.0.0 --port 8000
