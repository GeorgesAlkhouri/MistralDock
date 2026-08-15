# MistralDock

MistralDock is a small external Mistral OCR sidecar for Paperless-ngx. It receives Paperless workflow webhooks, retrieves the original file only through Paperless REST API, replaces `content` with Mistral OCR Markdown, and safely proposes or writes `title`, `created`, and existing `tags`.

It never reads the Paperless database/filesystem, creates no Paperless tags, changes no PDF, and defaults to a no-write dry-run mode.

## Deploy

1. Create the persistent Docker network used by the existing Paperless Compose project, or change `paperless` in `compose.example.yml` to its actual network name.
2. Copy the environment template and fill the four required values without committing the result:

   ```sh
   cp .env.example .env
   docker compose -f compose.example.yml up -d --build
   ```

3. Keep `WRITE_MODE=dry-run` for the initial test set. The authenticated run endpoint shows the proposed title, date, tags, validation warnings, page count, and error codes without storing full OCR text.

The container is non-root, uses a persistent volume only for its own SQLite state, and creates temporary documents only under its in-memory `/tmp` filesystem.

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
