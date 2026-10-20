#!/bin/sh
# Apply every SQL file in supabase/migrations, in order, to the database in SUPABASE_DB_URL
# (read from .env, never printed). The migrations are idempotent, so re-running is safe.
#
#   ./scripts/apply-migrations.sh
#
# SUPABASE_DB_URL: Supabase dashboard -> Connect -> "Session pooler" URI, with your database password.
set -eu
cd "$(dirname "$0")/.."

DB_URL=$(grep '^SUPABASE_DB_URL=' .env 2>/dev/null | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")
if [ -z "${DB_URL:-}" ]; then
  echo "SUPABASE_DB_URL is not set in .env" >&2
  exit 1
fi
if ! command -v psql >/dev/null 2>&1; then
  echo "psql is not installed (macOS: brew install libpq && brew link --force libpq)" >&2
  exit 1
fi

for file in supabase/migrations/*.sql; do
  echo "Applying $file"
  psql "$DB_URL" -v ON_ERROR_STOP=1 -q -X -f "$file"
done
echo "Migrations applied."
