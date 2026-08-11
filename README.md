# Filmocabulary

Filmocabulary is a Django application for building advanced English vocabulary from
movies. It generates B2-C2 words, phrasal verbs, idioms, and collocations with English
definitions and spoiler-safe example sentences, then turns saved vocabulary into
multi-movie fill-in-the-blank quizzes.

## Features

- OpenAI, Gemini, and Fireworks LLM providers with structured, validated output
- Automatic English subtitle lookup through OpenSubtitles
- Optional `.srt` or `.txt` upload when automatic lookup is unavailable
- Local subtitle filtering and per-user caching to reduce repeated input-token usage
- Saved movie library with vocabulary management
- Multi-movie quizzes, immediate feedback, scores, and attempt history
- HTMX and jQuery interactions without full-page reloads

## Stack

- Python 3.11+
- Django 5.2
- SQLite for development; PostgreSQL for production
- HTMX, jQuery, and custom CSS
- Pydantic for LLM response validation

## Local Setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py runserver
```

Open <http://127.0.0.1:8000/> and create an account.

On macOS or Linux, activate the environment with `source .venv/bin/activate` and copy
the environment template with `cp .env.example .env`.

## Configuration

`DJANGO_SECRET_KEY` is mandatory. Generate a local value instead of using the placeholder
from `.env.example`.

Select one vocabulary provider in `.env`:

```dotenv
VOCABULARY_LLM_PROVIDER=fireworks
FIREWORKS_API_KEY=your-key
```

Supported values are `openai`, `gemini`, and `fireworks`. Only the selected provider's
API key is required.

Automatic subtitle grounding is optional:

```dotenv
VOCABULARY_AUTO_SOURCE_PROVIDER=opensubtitles
OPENSUBTITLES_API_KEY=your-key
OPENSUBTITLES_USER_AGENT=Filmocabulary v1.0
```

Set `VOCABULARY_AUTO_SOURCE_PROVIDER=` to disable automatic lookup. Users can still
upload `.srt` or `.txt` source files, and generation falls back to model knowledge when
no source is available.

Never commit `.env` or paste API keys into logs, issues, or chat messages.

## Generation Behavior

Generation is deliberately single-shot. For a request of `N` saved entries, the service
asks for `N + 15` candidates, validates and source-checks them, and saves the first `N`
usable results. If fewer candidates pass validation, the valid subset is saved without a
second LLM request.

Subtitle text is reduced to complete dialogue units containing likely B2-C2 vocabulary
and cached on the user's movie record. Subsequent generations for the same movie reuse
that filtered source instead of downloading the subtitle again.

## Tests

```powershell
python manage.py test
python manage.py check
python manage.py makemigrations --check --dry-run
```

## Production

Production settings require `DJANGO_SETTINGS_MODULE=config.settings.production`,
`DATABASE_URL`, `ALLOWED_HOSTS`, and a strong `DJANGO_SECRET_KEY`. Static files are served
through WhiteNoise, and secure cookies, HTTPS redirect, HSTS, and other Django security
headers are enabled.

```bash
python manage.py migrate
python manage.py collectstatic --noinput
gunicorn config.wsgi:application
```
