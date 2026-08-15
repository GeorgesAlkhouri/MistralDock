# MistralDock

MistralDock is a small external [Mistral Document AI](https://docs.mistral.ai/studio-api/document-processing/basic_ocr) sidecar for [Paperless-ngx](https://docs.paperless-ngx.com/). It receives Paperless workflow webhooks, retrieves originals only through the Paperless REST API, and replaces `content` with Mistral OCR Markdown while safely proposing or writing `title`, `created`, and existing `tags`.

It never reads the Paperless database or filesystem, creates no Paperless tags, changes no PDF, and starts in no-write dry-run mode.

## What it does

1. Paperless emits a **Document Added** webhook with the document ID.
2. MistralDock downloads the original and the currently visible Paperless tags through official APIs.
3. PDFs are split into bounded page chunks; each is OCRed with Mistral and its structured document annotations.
4. MistralDock validates OCR and metadata, preserves existing tags, and performs one version-aware Paperless update only in `WRITE_MODE=live`.

Large documents are processed chunk-by-chunk. No OCR text or document bytes are retained in the sidecar database, logs, metrics, or status API.

## Quick start

1. Create the persistent Docker network used by the existing Paperless Compose project, or change `paperless` in `compose.example.yml` to its actual network name.
2. Copy the environment template and fill the required values without committing the result:

   ```sh
   cp .env.example .env
   docker compose -f compose.example.yml up -d --build
   ```

3. Keep `WRITE_MODE=dry-run` for the initial test set. The authenticated run endpoint shows the proposed title, date, tags, validation warnings, page count, and error codes without storing full OCR text.

The container is non-root, uses a persistent volume only for its own SQLite state, and creates temporary documents only under its in-memory `/tmp` filesystem.

Required environment values are `PAPERLESS_URL`, `PAPERLESS_TOKEN`, `MISTRAL_API_KEY`, and `MISTRALDOCK_API_TOKEN`. Use a new, random `MISTRALDOCK_API_TOKEN`; do not reuse either provider credential for webhook authentication.

## Paperless configuration

Create a dedicated Paperless user/token with these minimum global permissions and object access:

- View and change the target Paperless documents;
- View all tags that MistralDock may select.

The account must be able to see those particular documents and tags through Paperless object-level permissions; an API call returning zero documents/tags cannot process or classify anything.

Create a Paperless **Document Added** workflow action:

- URL: `http://mistraldock:8080/v1/webhooks/paperless`
- Encoding: JSON
- Body: `{"document_id": {{doc_id}}}`
- Header: `Authorization: Bearer <MISTRALDOCK_API_TOKEN>`

Ensure Paperless permits internal webhook requests when its Docker network is used. The Paperless service must share the `paperless` network with MistralDock.

## Operations

The following endpoints use `Authorization: Bearer <MISTRALDOCK_API_TOKEN>`:

- `POST /v1/webhooks/paperless` accepts a Document Added webhook.
- `POST /v1/documents/{id}/reprocess` forces a new run for the latest document version.
- `GET /v1/documents/{id}/runs` returns safe run metadata without document bytes or OCR text.

`GET /health/live`, `GET /health/ready`, and `GET /metrics` are unauthenticated and therefore should remain internal to the Docker network or be exposed only through a monitoring proxy.

Switch to `WRITE_MODE=live` only after reviewing 20–50 representative dry runs (including poor scans and documents above eight pages). Roll back immediately by setting `WRITE_MODE=dry-run` or stopping the MistralDock container; Paperless itself is never modified.

## Development

MistralDock targets Python 3.13 and uses [uv](https://docs.astral.sh/uv/):

```sh
uv sync --all-groups
uv run pytest -q
uv run ruff check .
```

The test suite uses fake Paperless and Mistral providers; it does not require live credentials. Before enabling production writes, run the documented dry-run pilot against representative Paperless documents.

## Security and contributing

- Please read [SECURITY.md](SECURITY.md) before reporting a vulnerability.
- Contributions, local checks, and scope expectations are documented in [CONTRIBUTING.md](CONTRIBUTING.md).
- Never commit `.env` files, API keys, Paperless tokens, OCR text, or source documents.

## License

MistralDock is released under the [MIT License](LICENSE).
