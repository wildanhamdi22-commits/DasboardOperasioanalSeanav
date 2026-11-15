#!/bin/sh
# Start-up untuk Docker / Railway / Render / VPS
set -e
python manage.py init_database
python manage.py migrate --noinput
exec gunicorn seanav_web.wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-3}" \
  --timeout 180 \
  --access-logfile - --error-logfile -
