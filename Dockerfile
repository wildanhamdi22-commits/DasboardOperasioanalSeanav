# Seanav Operations — image produksi (Django + Gunicorn + LibreOffice untuk PDF)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PDF_CONVERTER=libreoffice \
    DJANGO_DEBUG=False

RUN apt-get update \
 && apt-get install -y --no-install-recommends libreoffice-writer-nogui fonts-dejavu fonts-liberation fonts-crosextra-carlito \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN DJANGO_SECRET_KEY=collectstatic-only-not-used-at-runtime-0123456789 python manage.py collectstatic --noinput \
 && useradd --create-home seanav && mkdir -p /app/media && chown -R seanav /app/media
USER seanav

EXPOSE 8000
CMD ["sh", "scripts/start.sh"]
