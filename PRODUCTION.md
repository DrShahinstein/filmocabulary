# Home Production

Filmocabulary's production profile is designed for one household or a small trusted group.
It runs on Linux, macOS, or Windows with SQLite, a single-process async Uvicorn server, WhiteNoise
static files, and the existing installation-wide `LLM_*` configuration. It does not require
PostgreSQL, Redis, Docker, a public domain, or a public Internet deployment.

Production differs from Django's development server in important ways: debug pages are
disabled, hostnames are validated, static assets use a versioned manifest, registrations
are closed by default, and startup refuses placeholder secrets.

### Install and configure

Create the virtual environment as in Local Setup, then install the Home server dependency:

```bash
python -m pip install -r requirements-production.txt
```

Generate a deployment secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Put that value in `DJANGO_SECRET_KEY` in `.env`. Keep the existing `LLM_*` values. For use
only on this computer, these Home values are sufficient:

```dotenv
# .env
ALLOWED_HOSTS=localhost,127.0.0.1
HOME_BIND=127.0.0.1:8000
HOME_HTTPS=False
SIGNUP_ENABLED=False
RATELIMIT_ENABLE=True
```

Prepare the database and static files, create the first account, and start the server:

```bash
chmod +x scripts/home
scripts/home prepare
scripts/home manage createsuperuser
scripts/home start
```

Open <http://127.0.0.1:8000/>. `scripts/home start` safely repeats migrations, static-file
collection, and Django's configuration check before starting Uvicorn.

### Trusted LAN access

Give the server computer a stable address through the router, then change `.env`:

```dotenv
ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.50,filmocabulary.local
HOME_BIND=0.0.0.0:8000
```

Restart Filmocabulary and open `http://192.168.1.50:8000/` from another device. Permit port
8000 only on the trusted private network in the computer's firewall. Do not configure
router port forwarding or expose this service directly to the public Internet.

For remote access, a private overlay network such as Tailscale is preferable to opening a
router port. If Tailscale Serve or another trusted reverse proxy provides HTTPS, set:

```dotenv
ALLOWED_HOSTS=your-private-hostname
CSRF_TRUSTED_ORIGINS=https://your-private-hostname
HOME_BIND=127.0.0.1:8000
HOME_HTTPS=True
TRUST_X_FORWARDED_PROTO=True
```

`HOME_HTTPS=True` enables HTTPS redirects, secure cookies, and HSTS together. Do not enable
it when connecting directly over plain HTTP, or the browser will be redirected to an HTTPS
service that does not exist.

### Accounts and registration

Home production disables self-registration by default. Add household members through
`/admin/`, or temporarily set `SIGNUP_ENABLED=True`, restart, create the required accounts,
and disable it again. Development mode continues to allow signup regardless of this value.

### Backups and restoration

Create a transactionally consistent SQLite snapshot while the server is running:

```bash
scripts/home backup
scripts/home backup /path/on/another/device/filmocabulary.sqlite3
```

Default backups are written under `backups/`, excluded from Git, and readable only by their
owner. Regularly copy them to another device. To restore, stop Filmocabulary, preserve the
current `db.sqlite3`, copy the selected backup into its place, and run `scripts/home prepare`
before restarting.

If `SQLITE_DATABASE_PATH` is configured, restore to that path instead. Never copy the live
SQLite database directly while the server is writing; use `scripts/home backup` to create a
consistent snapshot.

### Home command reference

```text
scripts/home start                 Prepare and run the Home server
scripts/home prepare               Apply migrations, collect static files, and check config
scripts/home backup [destination]  Create a consistent SQLite backup
scripts/home manage <command>      Run any Django management command in Home mode
```

Home production runs on Windows, macOS, and Linux. Uvicorn is a pure-Python ASGI server,
so the same Home server starts on every platform without a POSIX-only dependency. The
`scripts/home` wrapper is a POSIX shell script; on Windows use Git Bash, WSL, or run the
equivalent commands directly: `manage.py migrate`, `manage.py collectstatic`,
`manage.py check`, then `python -m config.uvicorn`.
