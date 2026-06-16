#!/bin/sh
set -e

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  if [ -z "${DATABASE_URL:-}" ] && [ -z "${DATABASE_POOLER_URL:-}" ] && [ -z "${DATABASE_PRIVATE_URL:-}" ] && [ -z "${PGHOST:-}" ]; then
    echo "ERROR: DATABASE_URL is not set. Add DATABASE_URL to your deployment environment before running migrations."
    exit 1
  fi
  echo "Running database migrations..."
  python -c "from app.config import resolve_database_url; resolve_database_url()" || exit 1
  alembic upgrade head
fi

exec "$@"
