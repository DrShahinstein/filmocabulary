# Agent Execution Guidelines for Filmocabulary

## Code Style & Architecture
1. **Django Rules:** Use Class-Based Views (CBVs) or clean Functional Views with HTMX decorator helpers. Always use standard Django forms or Pydantic models for request validation.
2. **HTMX First:** Avoid full page reloads for dynamic content. Use HTMX partial templates located in `templates/partials/`.
3. **LLM Output Safety:** Always validate LLM response JSON strictly before writing to the database. Use try/except blocks around external API calls.
4. **Security:** Never generate code that exposes API keys or bypasses Django CSRF protection.

## Git & Branching Workflow
1. **Protected Main Branch:** Never commit directly to `main`. All active development must take place on the `dev` branch (or feature branches created off `dev`).
2. **Autonomous Commits:** Upon successfully completing and verifying a task (e.g., passing tests), automatically create a Git commit.
3. **Commit Message Format:** Use structured, semantic prefixes (`feat:`, `fix:`, `refactor:`, `docs:`). Do NOT use emojis.
