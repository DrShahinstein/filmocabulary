# Filmocabulary

Filmocabulary is a smart vocabulary learning web-app that transforms film subtitles into personalized study materials. It automatically extracts rare and useful vocabulary from films, complete with definitions and real context, and helps you retain them using an algorithmic Spaced Repetition System (SRS).

## Features

- Universal OpenAI-compatible LLM integration with structured, validated output
- Automatic English subtitle lookup through OpenSubtitles
- Optional `.srt` or `.txt` upload when automatic lookup is unavailable
- Local subtitle filtering and per-user caching to reduce repeated input-token usage
- Saved movie library with vocabulary management
- Definition, fill-in-the-blank, and mixed practice modes
- Part-of-speech-aware distractors for five-option definition and cloze questions
- Definition or cloze practice for any Words Explorer filter, without changing progress
- A focused Learning Pool plus new, learning, and mastered progress tracking
- HTMX and native JavaScript interactions without full-page reloads

## Stack

- Python 3.11+
- Django 5.2
- SQLite
- HTMX, native JavaScript, and custom CSS
- Pydantic for LLM response validation

## Local Setup

Make sure you've configured `.env` before running the server.

### Linux and macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
cp .env.example .env
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

If PowerShell blocks activation:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Configuration

Vocabulary generation is powered by LLM. Connect your own model with your API key.
Any provider that supports openai-compatible chat completions and json schema structured outputs are allowed.
Configure the API key, model, and provider base URL.

Example for Fireworks:

```dotenv
# .env
...
LLM_API_KEY=your-fireworks-key
LLM_MODEL=accounts/fireworks/models/your-model-name
LLM_BASE_URL=https://api.fireworks.ai/inference/v1
LLM_REASONING_EFFORT=none
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

See `.env.example` for descriptions, OpenAI/Gemini/custom-provider examples, and subtitle
filter presets.

## Generation Behavior

Filmocabulary normally makes one structured LLM request. It asks for a few extra
candidates so weak, duplicated, or invalid entries can be removed without affecting the
rest of the batch. Only vocabulary that passes validation is saved. If fewer words qualify
than requested, the good results are kept and the system does not make a refill request.

<br/>

### Optional Editorial Review

Editorial review makes a second LLM request to inspect the first result. It can clean up
headwords, improve CEFR ratings, and remove vocabulary that is too ordinary, too
specialized, or not broadly useful.

It can provide a better final deck, especially when the first extraction is noisy, but it
is not required for vocabulary generation to work well. Think of it as an optional quality
control rather than a necessary step.

A 100-word generation commonly uses around `12,000-15,000` tokens without editorial
review. When review is enabled, allow roughly `25,000-30,000` tokens for the same job.
Actual usage varies by movie, model, subtitle source, and the number of cards retained.

Enable it when the extra quality is worth the additional tokens:

```env
LLM_EDITORIAL_REVIEW=True
```

<br/>

### Subtitle Preparation

When subtitles are available, Filmocabulary cleans and shortens them before sending them
to the model. It removes timestamps and low-value dialogue while keeping complete
sentences, phrasal verbs, idioms, and useful context. The filtered source is cached so it
does not need to be downloaded and prepared again for every request.

Each generated card is checked independently. One bad card does not discard the others,
and an item can still be used in definition quizzes even when a safe cloze sentence cannot
be created for it. Usage logs record counts and token usage without recording subtitle or
generated vocabulary content.

## Tests

```powershell
python manage.py test
python manage.py check
python manage.py makemigrations --check --dry-run
```

## Prompt Benchmarking

The benchmark command uses the same vocabulary extraction, Python pre-filtering,
grounding, and validation pipeline as the application, while running entirely in
memory and independently of the SQLite database. This makes it easy to inspect and
compare generated vocabulary while refining prompts.

```bash
python manage.py benchmark_prompt --help
python manage.py benchmark_prompt -m "The Matrix" -y 1999 -l 100 --words-only -o ../the_matrix_1999.json
```

Without `--output`, the command pretty-prints the artifact to the console. With an output
path, it atomically creates or replaces that JSON file and prints only a short summary.
Full artifacts report extraction yield, reviewed yield, and editorial filtering
separately, so prompt iterations remain comparable when the review stage removes cards.

Without `--source-file`, the command attempts the configured OpenSubtitles acquisition
path, pre-filters the downloaded English subtitles entirely in memory, and reports the
provider, IMDb ID, subtitle source ID, and source fingerprint. It never reads or writes
the application's subtitle cache. Acquisition failures are reported before the command
visibly falls back to model knowledge. Local sources must be `.txt` or `.srt`; they use
the same filtering pipeline and bypass automatic acquisition.

Use `--words-only` to write
or print the accepted vocabulary array without the metadata wrapper when comparing prompt
variants side by side; source provenance is still printed to the terminal.

## Home Production

See [PRODUCTION.md](PRODUCTION.md)
