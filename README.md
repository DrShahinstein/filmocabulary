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

Generation is deliberately single-shot. For a request of `N` saved entries, the service
adds `ceil(N × 20%)` candidates, bounded between 3 and 15, then validates and
source-checks the response and saves the first `N` usable results. If fewer candidates pass
validation, the valid subset is saved without a second LLM request.

Malformed candidates are validated independently, so one bad item does not discard valid
siblings. Cloze blanks are derived when an example contains one exact or inflected use of
the term, but cloze eligibility is optional and never prevents a grounded item from being
used in the definition-based quiz. Candidate-yield logs separate schema, grounding,
duplicate, and cloze-ineligibility diagnostics without recording generated content.

### Prompt Benchmarking

Use the database-free benchmarking command to iterate on extraction prompts from the
terminal. It uses the configured `LLM_*` provider and the same schema validation,
grounding, duplicate checks, and cloze derivation as normal generation, but never creates
movies or vocabulary records.

```bash
python manage.py benchmark_prompt --movie "Inception"
python manage.py benchmark_prompt -m "Inception" -l 50 -o benchmarks/inception_v1.json
python manage.py benchmark_prompt -m "Inception" -f transcripts/inception.srt -o benchmarks/inception_grounded.json
```

Without `--output`, the command pretty-prints the artifact to the console. With an output
path, it atomically creates or replaces that JSON file and prints only a short summary.
Artifacts contain accepted items, candidate and rejection counts, detailed schema and
cloze-ineligibility categories, configured model metadata, a system-prompt fingerprint,
and optional filtered-source metadata. The candidate limit defaults to 50 and accepts
values from 1 through 115. Local sources must be `.txt` or `.srt`; they are parsed and
pre-filtered through the production source pipeline before being sent to the model.

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

## Home Production

See [PRODUCTION.md](PRODUCTION.md)
