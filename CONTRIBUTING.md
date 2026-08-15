# Contributing to MistralDock

Thanks for helping improve MistralDock.

## Development setup

MistralDock requires Python 3.13 and uses [uv](https://docs.astral.sh/uv/).

```sh
uv sync --all-groups
uv run pytest -q
uv run ruff check .
```

The automated tests use provider fakes. Do not add real API keys, Paperless tokens, OCR text, PDFs, or other private documents to the repository or test fixtures.

## Change guidelines

- Keep the service external to Paperless-ngx; use only official Paperless REST APIs.
- Preserve the v1 scope: no direct database/filesystem access, no new Paperless tags, and no source-PDF modifications.
- Add or update a focused test for every behavior change.
- Keep `WRITE_MODE=dry-run` as the safe default.
- Run the full test suite and Ruff before opening a pull request.

## Pull requests

Use a concise title, explain the user-visible effect and validation performed, and keep unrelated changes out of the same pull request. For security-sensitive findings, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.
