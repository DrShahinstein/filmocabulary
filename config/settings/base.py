import mimetypes
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

mimetypes.add_type("image/webp", ".webp", strict=True)

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env(
    DEBUG=(bool, False),
    DJANGO_SECRET_KEY=str,
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    LLM_API_KEY=(str, ""),
    LLM_MODEL=(str, "gpt-4.1-mini"),
    LLM_BASE_URL=(str, "https://api.openai.com/v1"),
    LLM_TIMEOUT_SECONDS=(float, 180.0),
    LLM_TEMPERATURE=(str, ""),
    LLM_REASONING_EFFORT=(str, ""),
    LLM_MAX_TOKENS_PARAMETER=(str, "max_tokens"),
    VOCABULARY_SOURCE_MAX_BYTES=(int, 2 * 1024 * 1024),
    VOCABULARY_FILTER_MAX_WORDS=(int, 1100),
    VOCABULARY_FILTER_MAX_CHARACTERS=(int, 6000),
    VOCABULARY_AUTO_SOURCE_PROVIDER=(str, "opensubtitles"),
    OPENSUBTITLES_API_KEY=(str, ""),
    OPENSUBTITLES_USER_AGENT=(str, "Filmocabulary v1.0"),
    OPENSUBTITLES_TIMEOUT_SECONDS=(float, 20.0),
    VOCABULARY_DEFAULT_ITEM_COUNT=(int, 12),
    VOCABULARY_GENERATION_RATE=(str, "5/h"),
    RATELIMIT_ENABLE=(bool, True),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY.strip():
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set and non-empty.")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "movies",
    "vocabulary",
    "quizzes",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

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
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Istanbul"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "movies:dashboard"
LOGOUT_REDIRECT_URL = "login"

LLM_API_KEY = env("LLM_API_KEY")
LLM_MODEL = env("LLM_MODEL")
LLM_BASE_URL = env("LLM_BASE_URL")
LLM_TIMEOUT_SECONDS = env("LLM_TIMEOUT_SECONDS")
LLM_TEMPERATURE = env("LLM_TEMPERATURE")
LLM_REASONING_EFFORT = env("LLM_REASONING_EFFORT")
LLM_MAX_TOKENS_PARAMETER = env("LLM_MAX_TOKENS_PARAMETER")
VOCABULARY_SOURCE_MAX_BYTES = env("VOCABULARY_SOURCE_MAX_BYTES")
VOCABULARY_FILTER_MAX_WORDS = env("VOCABULARY_FILTER_MAX_WORDS")
VOCABULARY_FILTER_MAX_CHARACTERS = env("VOCABULARY_FILTER_MAX_CHARACTERS")
VOCABULARY_AUTO_SOURCE_PROVIDER = env("VOCABULARY_AUTO_SOURCE_PROVIDER")
OPENSUBTITLES_API_KEY = env("OPENSUBTITLES_API_KEY")
OPENSUBTITLES_USER_AGENT = env("OPENSUBTITLES_USER_AGENT")
OPENSUBTITLES_TIMEOUT_SECONDS = env("OPENSUBTITLES_TIMEOUT_SECONDS")
VOCABULARY_DEFAULT_ITEM_COUNT = env("VOCABULARY_DEFAULT_ITEM_COUNT")
VOCABULARY_GENERATION_RATE = env("VOCABULARY_GENERATION_RATE")
RATELIMIT_ENABLE = env("RATELIMIT_ENABLE")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "loggers": {
        "vocabulary.usage": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
