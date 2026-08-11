# Agent Execution Guidelines for Filmocabulary

## Code Style & Architecture
1. **Django Rules:** Use Class-Based Views (CBVs) or clean Functional Views with HTMX decorator helpers. Always use standard Django forms or Pydantic models for request validation.
2. **HTMX First:** Avoid full page reloads for dynamic content. Use HTMX partial templates located in `templates/partials/`.
3. **LLM Output Safety:** Always validate LLM response JSON strictly before writing to the database. Use try/except blocks around external API calls.
4. **Security:** Never generate code that exposes API keys or bypasses Django CSRF protection.

## Git & Commits
- Commit messages should be structured: `feat:`, `fix:`, `refactor:`, `docs:`.
- Do not prefer emoji.