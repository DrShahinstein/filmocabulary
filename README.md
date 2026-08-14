# Filmocabulary

Filmocabulary is a smart vocabulary learning web-app that transforms film subtitles into personalized study materials. It automatically extracts rare and useful vocabulary from films, complete with definitions and real context, and helps you retain them using an algorithmic Spaced Repetition System (SRS).

## Features

- Universal OpenAI-compatible LLM integration with structured, validated output
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

Vocabulary generation is powered by a universal OpenAI-compatible client. Connect any
provider that supports the Chat Completions API and JSON Schema structured outputs by
setting its API key, model, and base URL. There is no provider allowlist or
provider-specific application code.

Example:
```dotenv
# .env
...
LLM_API_KEY=your-key
LLM_MODEL=your-provider-model-name
LLM_BASE_URL=https://your-provider.example/v1
...
```

`LLM_BASE_URL` defaults to `https://api.openai.com/v1`, so an OpenAI configuration
only needs `LLM_API_KEY` and `LLM_MODEL`. Other compatible services use their published
base URL. For example:

```dotenv
# Gemini OpenAI compatibility endpoint
LLM_API_KEY=your-gemini-key
LLM_MODEL=gemini-3.6-flash
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
```

```dotenv
# Fireworks OpenAI compatibility endpoint
LLM_API_KEY=your-fireworks-key
LLM_MODEL=accounts/fireworks/models/deepseek-v4-flash-0731
LLM_BASE_URL=https://api.fireworks.ai/inference/v1
LLM_REASONING_EFFORT=none
```

Generic optional request controls preserve model-specific requirements without adding
provider branches:

```dotenv
LLM_TIMEOUT_SECONDS=180
LLM_TEMPERATURE=
LLM_REASONING_EFFORT=
LLM_MAX_TOKENS_PARAMETER=max_tokens
```

Leave temperature or reasoning effort blank to use the provider's default. Set
`LLM_MAX_TOKENS_PARAMETER=max_completion_tokens` for models that require the newer
Chat Completions parameter. The completion budget still scales as `512 + 160` tokens
per requested candidate. The previous `VOCABULARY_LLM_PROVIDER`, `OPENAI_*`, `GEMINI_*`,
and `FIREWORKS_*` variables are no longer read.

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

The generic LLM client applies the same completion allowance to every compatible provider
(`512 + 160` tokens per requested candidate). Reasoning remains disabled when
`LLM_REASONING_EFFORT=none` is configured. Usage logs record prompt, completion, total,
and candidate-yield counts without logging subtitle or response content.

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
