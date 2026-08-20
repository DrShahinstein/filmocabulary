"""Uvicorn Home server configuration and launcher.

Uvicorn replaces Gunicorn as the production server so the Home profile runs on
every platform, including Windows. It serves Django's ASGI application in a
single process: one worker preserves consistent SQLite writes and the in-memory
rate-limit state, while the async loop and Django's thread pool allow browser
and LLM requests to make progress concurrently.
"""

from pathlib import Path

import environ
import uvicorn

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    HOME_BIND=(str, "127.0.0.1:8000"),
)
environ.Env.read_env(BASE_DIR / ".env")


def _parse_bind(value):
    """Split a 'host:port' HOME_BIND value into host and integer port."""
    value = value.strip()
    if value.count(":") != 1:
        raise ValueError(
            f"HOME_BIND must be 'host:port', got {value!r}. "
            "IPv6 addresses and Unix sockets are not supported."
        )
    host, port = value.rsplit(":", 1)
    if not port.isdigit():
        raise ValueError(f"HOME_BIND port must be an integer, got {port!r}.")
    return host.strip(), int(port)


def main():
    host, port = _parse_bind(env("HOME_BIND"))
    uvicorn.run(
        "config.asgi:application",
        host=host,
        port=port,
        workers=1,
        access_log=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
