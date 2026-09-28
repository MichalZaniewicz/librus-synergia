# Contributing

Thanks for helping! English or Polish are both fine.

## Found something Librus does differently?

That's the most valuable kind of report. Use the **"Librus API changed"** form and share the **shape** of the JSON (keys and value types), with names, ids and text replaced by placeholders. That's how the [API notes](https://michalzaniewicz.github.io/librus-synergia/) stay correct.

## Development

```bash
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
ruff check src tests && ruff format --check src tests
mypy
```

The API notes are a small [MkDocs](https://www.mkdocs.org/) site:

```bash
pip install -e ".[docs]"
mkdocs serve
```

## Pull requests

- Keep a PR to one change, with a test. The tests mock HTTP, so they never touch a real account.
- Mark every new claim in `docs/` as ✅ confirmed live, 📖 taken from another client, or ❓ unverified. Don't upgrade something to ✅ without having seen it in a real response.
- Update `CHANGELOG.md`.
- **Never commit credentials, cookies, session files or real children's data.**
- When testing against a real account, use **normal** logins only: no scripted wrong passwords or rapid retries. It's a real family's school account and Librus may have abuse protection.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
