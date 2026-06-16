#!/bin/sh
set -e

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  if [ -z "${DATABASE_URL:-}" ] && [ -z "${DATABASE_PRIVATE_URL:-}" ] && [ -z "${PGHOST:-}" ]; then
    echo "ERROR: DATABASE_URL is not set. Add DATABASE_URL to your deployment environment before running migrations."
    exit 1
  fi
  echo "Running database migrations..."
  alembic upgrade head
fi

exec "$@"
