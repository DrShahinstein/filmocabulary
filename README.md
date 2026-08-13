# Filmocabulary

Filmocabulary is a smart vocabulary learning web-app that transforms film subtitles into personalized study materials. It automatically extracts rare and useful vocabulary from films, complete with definitions and real context, and helps you retain them using an algorithmic Spaced Repetition System (SRS).

## Features

- OpenAI, Gemini, and Fireworks LLM providers with structured, validated output
- Automatic English subtitle lookup through OpenSubtitles
- Optional `.srt` or `.txt` upload when automatic lookup is unavailable
- Local subtitle filtering and per-user caching to reduce repeated input-token usage
- Saved movie library with vocabulary management
- Five-option multiple-choice practice with part-of-speech-aware distractors
- A focused Learning Pool plus new, learning, and mastered progress tracking
- HTMX and native JavaScript interactions without full-page reloads

## Stack

- Python 3.11+
- Django 5.2
- SQLite for development; PostgreSQL for production
- HTMX, native JavaScript, and custom CSS
- Pydantic for LLM response validation

## Local Setup

Make sure you've configured `.env` before running the server.

### Linux and macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

If PowerShell blocks activation:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Configuration

Vocabulary generation is powered by an LLM. To use it, connect your own model with its API key.

Example:
```dotenv
# .env
...
VOCABULARY_LLM_PROVIDER=fireworks
FIREWORKS_API_KEY=your-key
...
```

Automatic subtitle grounding is optional but also recommended:
```dotenv
# .env
...
VOCABULARY_AUTO_SOURCE_PROVIDER=opensubtitles
OPENSUBTITLES_API_KEY=your-key
OPENSUBTITLES_USER_AGENT=Filmocabulary v1.0
...
```

Set `VOCABULARY_AUTO_SOURCE_PROVIDER=` to disable automatic lookup. Users can still upload `.srt` or `.txt` source files, and generation falls back to model knowledge when no source is available.

Never commit `.env` or paste API keys into logs, issues, or chat messages.

## Generation Behavior

Generation is deliberately single-shot. For a request of `N` saved entries, the service
asks for `N + 15` candidates, validates and source-checks them, and saves the first `N`
usable results. If fewer candidates pass validation, the valid subset is saved without a
second LLM request.

### Token Strategy

The largest avoidable input cost is normally the full subtitle file. Filmocabulary reduces
that cost before generation:

1. It checks the current user's movie cache before calling OpenSubtitles.
2. It parses subtitles into complete dialogue units, preserving sentence context.
3. It keeps units containing locally recognized B1-C2 terms and prioritizes higher
   levels within a request-sized budget. The default character envelope scales from
   roughly `4,000` for very small requests to `6,000` at 30 items, `8,000` at 50,
   and `14,000` at 100.
4. It caches a filtered large-request envelope on the movie, then derives the smaller
   request-sized source from that cache without reintroducing raw or A1-A2 dialogue.

The filter removes timestamps and elementary-only dialogue without stripping individual
words from retained sentences, so phrasal verbs, idioms, and surrounding context remain
intact. A versioned negative cache also avoids repeating subtitle work when no suitable
advanced source text is found.

For Fireworks, reasoning is disabled and the completion allowance scales with the candidate
count (`512 + 160` tokens per requested candidate). Usage logs record prompt, completion,
total, and candidate-yield counts without logging subtitle or response content.

Together, these measures reduce repeated downloads and unnecessary input and output tokens.
Actual usage still varies by movie, provider, requested entry count, and cache availability,
so the application does not promise a fixed token count or percentage reduction.

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

Production-only dependencies are kept out of the local SQLite setup. Install them with:

```bash
python -m pip install -r requirements-production.txt
```

`requirements-production.txt` uses Psycopg's binary distribution to avoid compiler and
PostgreSQL-header requirements. Gunicorn is installed only on POSIX systems because it
does not support Windows; Windows remains fully supported for local Django development.

```bash
python manage.py migrate
python manage.py collectstatic --noinput
gunicorn config.wsgi:application
```
