from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import env

DEBUG = False

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "Home production requires explicit ALLOWED_HOSTS; the '*' wildcard is not allowed."
    )
if len(SECRET_KEY) < 50 or SECRET_KEY == "replace-with-a-long-random-secret":  # noqa: F405
    raise ImproperlyConfigured(
        "Home production requires a unique DJANGO_SECRET_KEY of at least 50 characters."
    )

# SQLite is the canonical Home database. WAL permits readers while a write is in
# progress, and IMMEDIATE transactions wait at their boundary instead of failing
# later with a locked-database error. A single Uvicorn worker complements these
# settings; see config/uvicorn.py.
DATABASES = {  # noqa: F405
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(
            env.path("SQLITE_DATABASE_PATH", default=BASE_DIR / "db.sqlite3")  # noqa: F405
        ),
        "OPTIONS": {
            "timeout": 20,
            "transaction_mode": "IMMEDIATE",
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL",
        },
    }
}

MIDDLEWARE = [*MIDDLEWARE]  # noqa: F405
MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

HOME_HTTPS = env.bool("HOME_HTTPS", default=False)
CSRF_COOKIE_SECURE = HOME_HTTPS
SESSION_COOKIE_SECURE = HOME_HTTPS
SECURE_SSL_REDIRECT = HOME_HTTPS
if env.bool("TRUST_X_FORWARDED_PROTO", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000 if HOME_HTTPS else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = HOME_HTTPS
SECURE_HSTS_PRELOAD = HOME_HTTPS
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "filmocabulary-home",
    }
}

SIGNUP_ENABLED = env.bool("SIGNUP_ENABLED", default=False)
