#!/usr/bin/env bash
set -euo pipefail

DUMP_FILE="${1:-render_backup.dump}"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL must be set to the target database connection string." >&2
  exit 1
fi

if [[ ! -f "$DUMP_FILE" ]]; then
  echo "Backup file not found: $DUMP_FILE" >&2
  exit 1
fi

pg_restore --no-owner --no-acl --clean --if-exists --dbname "$DATABASE_URL" "$DUMP_FILE"
