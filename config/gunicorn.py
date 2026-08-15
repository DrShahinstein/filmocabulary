"""Gunicorn defaults for a small, trusted Home installation."""

from pathlib import Path

import environ


BASE_DIR = Path(__file__).resolve().parent.parent
env = environ.Env(
    HOME_BIND=(str, "127.0.0.1:8000"),
    HOME_THREADS=(int, 4),
    HOME_REQUEST_TIMEOUT=(int, 240),
)
environ.Env.read_env(BASE_DIR / ".env")

bind = env("HOME_BIND")
workers = 1
threads = env("HOME_THREADS")
timeout = env("HOME_REQUEST_TIMEOUT")
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
capture_output = True
