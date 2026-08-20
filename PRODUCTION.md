# Filmocabulary Home Server Setup

This profile runs Filmocabulary for one person or a small trusted household.
It uses SQLite, Uvicorn, and WhiteNoise; PostgreSQL, Redis, Docker, and a public
domain are not required.

Use it on a trusted LAN or private network. Do not expose it directly to the
public Internet.

## 1. Install

Run these commands from the project directory.

### Windows (PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-production.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(64))"
```

### Linux or macOS

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements-production.txt
cp .env.example .env
chmod +x scripts/home
./.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(64))"
```

The final command prints a private Django secret. Keep it for the next step.
Skip the copy command if an existing `.env` must be preserved.

## 2. Configure `.env`

Set the generated secret and these Home values:

```dotenv
DJANGO_SECRET_KEY=paste-the-generated-value-here
ALLOWED_HOSTS=localhost,127.0.0.1
HOME_BIND=127.0.0.1:8000
HOME_HTTPS=False
SIGNUP_ENABLED=False
RATELIMIT_ENABLE=True
```

Configure the `LLM_*` variables next. Configure `OPENSUBTITLES_*` too if you
want automatic subtitle retrieval. `.env.example` documents every option and
includes provider examples.

Production data uses `db.sqlite3` by default. To store it elsewhere, set
`SQLITE_DATABASE_PATH` to an absolute path whose parent directory exists.

## 3. Prepare, create an account, and start

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File scripts\home.ps1 prepare
powershell -ExecutionPolicy Bypass -File scripts\home.ps1 manage createsuperuser
powershell -ExecutionPolicy Bypass -File scripts\home.ps1 start
```

### Linux or macOS

```bash
scripts/home prepare
scripts/home manage createsuperuser
scripts/home start
```

Open <http://127.0.0.1:8000/> and sign in with the account just created.
`start` safely runs migrations, static-file collection, and configuration checks
again, so it is also the normal command after updating Filmocabulary.

## Let other trusted devices connect (LAN)

Find the server machine's IPv4 address (`ipconfig.exe` on Windows or `ip addr`
on Linux). For example, it might be `192.168.1.50`.

Then edit `.env`:

```dotenv
ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.50
HOME_BIND=0.0.0.0:8000
```

Restart the server and open `http://192.168.1.50:8000/` from another trusted
device.
Allow port 8000 only on the private network in the server firewall. Do not add
router port forwarding.

## Accounts

Home production keeps public signup closed by default.

The `createsuperuser` command in step 3 creates an administrator. While the
server is running, use `/admin/` to create and manage regular household users.
Alternatively, temporarily set `SIGNUP_ENABLED=True`, restart the server, let
the users register, and set it back to `False`.

## Backups

Create a consistent SQLite snapshot while the server is running:

```powershell
# Windows: default destination, then a custom destination
powershell -ExecutionPolicy Bypass -File scripts\home.ps1 backup
powershell -ExecutionPolicy Bypass -File scripts\home.ps1 backup D:\Backups\filmocabulary.sqlite3
```

```bash
# Linux/macOS: default destination, then a custom destination
scripts/home backup
scripts/home backup /mnt/backups/filmocabulary.sqlite3
```

Default backups go to the Git-ignored `backups/` directory, and existing files
are never overwritten. POSIX backups are owner-only. Windows backups inherit
the destination folder's ACL, so use a private user folder. Copy backups to
another device regularly.

To restore, stop the server, preserve the current database, copy the selected
backup to `db.sqlite3` (or `SQLITE_DATABASE_PATH`), run `prepare`, and start
again. Do not directly copy the live database while the server is writing.

## Optional private HTTPS or remote access

Prefer Tailscale or another private overlay network. When a trusted reverse
proxy terminates HTTPS, configure:

```dotenv
ALLOWED_HOSTS=your-private-hostname
CSRF_TRUSTED_ORIGINS=https://your-private-hostname
HOME_BIND=127.0.0.1:8000
HOME_HTTPS=True
TRUST_X_FORWARDED_PROTO=True
```

`HOME_HTTPS=True` enables HTTPS redirects, secure cookies, and HSTS together.
Leave it `False` for direct HTTP connections.

## Launcher commands

| Command | Action |
| --- | --- |
| `start` | Prepare and run the Home server |
| `prepare` | Migrate, collect static files, and check configuration |
| `backup [destination]` | Create a consistent SQLite snapshot |
| `manage <command>` | Run a Django management command |

Use `scripts\home.ps1` on Windows and `scripts/home` on Linux or macOS.
