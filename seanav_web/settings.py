"""Pengaturan Django Seanav Operations.

Semua nilai rahasia/lingkungan dibaca dari variabel lingkungan atau file `.env`
(lihat `.env.example`). Berlaku untuk lokal (Windows) maupun hosting (VPS/Railway/Render/Docker).
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", True)
_DEFAULT_SECRET = "local-development-only-change-before-deployment"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", _DEFAULT_SECRET)
if not DEBUG and (SECRET_KEY == _DEFAULT_SECRET or len(SECRET_KEY) < 32):
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY (min. 32 karakter acak) untuk mode produksi.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
# Domain bawaan platform hosting (otomatis bila tersedia)
for _var in ("RAILWAY_PUBLIC_DOMAIN", "RENDER_EXTERNAL_HOSTNAME"):
    _host = os.getenv(_var)
    if _host:
        ALLOWED_HOSTS.append(_host)
        CSRF_TRUSTED_ORIGINS.append(f"https://{_host}")


def _local_ipv4_addresses():
    """Alamat IPv4 komputer ini di jaringan lokal (LAN/Wi-Fi)."""
    import socket

    found = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))  # tidak mengirim data, hanya memilih adapter aktif
            found.add(probe.getsockname()[0])
    except OSError:
        pass
    try:
        found.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return sorted(ip for ip in found if not ip.startswith("127."))


# Mode lokal: izinkan akses dari komputer lain di jaringan kantor lewat http://<IP>:8000/
if DEBUG and env_bool("DJANGO_ALLOW_LAN", True):
    for _ip in _local_ipv4_addresses():
        if _ip not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(_ip)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "portal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "seanav_web.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "portal.context_processors.business_access",
                "portal.context_processors.portal_shell",
            ],
        },
    },
]

WSGI_APPLICATION = "seanav_web.wsgi.application"
ASGI_APPLICATION = "seanav_web.asgi.application"

# Database: DATABASE_URL (hosting) atau DB_* (lokal)
if os.getenv("DATABASE_URL"):
    import dj_database_url

    DATABASES = {"default": dj_database_url.parse(
        os.environ["DATABASE_URL"], conn_max_age=60, conn_health_checks=True,
        ssl_require=env_bool("DB_SSL_REQUIRE", False))}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME", "seanav_dev"),
            "USER": os.getenv("DB_USER", "postgres"),
            "PASSWORD": os.getenv("DB_PASSWORD", ""),
            "HOST": os.getenv("DB_HOST", "127.0.0.1"),
            "PORT": os.getenv("DB_PORT", "5432"),
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/"

LANGUAGE_CODE = "id"
TIME_ZONE = "Asia/Jakarta"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": (
        "django.contrib.staticfiles.storage.StaticFilesStorage" if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage")},
}
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.getenv("DJANGO_MEDIA_ROOT", BASE_DIR / "media"))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Upload: foto absensi dikirim sebagai data gambar (~1 MB), lampiran izin maks. 5 MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

GEOCODING_SEARCH_URL = os.getenv(
    "GEOCODING_SEARCH_URL", "https://nominatim.openstreetmap.org/search"
)

# Absensi
ABSENSI_JAM_MASUK = os.getenv("ABSENSI_JAM_MASUK", "08:00")
ABSENSI_TOLERANSI_MENIT = int(os.getenv("ABSENSI_TOLERANSI_MENIT", "15"))
ABSENSI_WAJIB_FOTO = env_bool("ABSENSI_WAJIB_FOTO", False)
# Titik kantor (default: Indonesia Stock Exchange Tower II, Jakarta). Kosongkan untuk menonaktifkan.
KANTOR_LAT = os.getenv("KANTOR_LAT", "-6.2237")
KANTOR_LNG = os.getenv("KANTOR_LNG", "106.8085")
KANTOR_RADIUS_M = int(os.getenv("KANTOR_RADIUS_M", "200"))

# Pembuatan PDF dokumen: "auto" (Word di Windows, LibreOffice di Linux), "word", atau "libreoffice"
PDF_CONVERTER = os.getenv("PDF_CONVERTER", "auto")
LIBREOFFICE_BIN = os.getenv("LIBREOFFICE_BIN", "soffice")

# Keamanan saat produksi (di belakang HTTPS)
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "0"))
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_AGE = int(os.getenv("DJANGO_SESSION_AGE", str(60 * 60 * 12)))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.getenv("DJANGO_LOG_LEVEL", "INFO")},
}
