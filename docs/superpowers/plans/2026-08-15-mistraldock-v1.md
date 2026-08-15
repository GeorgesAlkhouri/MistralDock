# MistralDock v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python 3.13 container service that receives Paperless webhooks, performs Mistral OCR and metadata extraction, and safely updates the same Paperless document through its REST API.

**Architecture:** FastAPI accepts authenticated requests and a single in-process worker consumes a durable SQLite queue. The processor talks only to Paperless API v10 and Mistral APIs, chunks PDFs into eight-page units, validates a three-field metadata schema, and either persists a dry-run proposal or applies one version-aware PATCH.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2/Alembic, httpx, mistralai, pypdf, pytest, respx, Docker.

**Spec:** `docs/superpowers/specs/2026-08-15-mistraldock-design.md`

## Global Constraints

- Run on Python 3.13; pin a reproducible dependency lockfile.
- Use only official Paperless REST API v10 and Mistral APIs; never mount or query Paperless storage/database.
- Keep `PAPERLESS_TOKEN`, `MISTRAL_API_KEY`, and `MISTRALDOCK_API_TOKEN` out of Git, logs, responses, and metrics.
- Support PDFs and PNG/JPG/JPEG/TIFF/BMP/GIF/WEBP; OCR each chunk at most once per processing attempt.
- Split PDFs into at most eight-page chunks; for multi-chunk documents consolidate metadata with one structured Mistral chat completion.
- Create no Paperless tags and preserve all existing tags by unioning valid suggestions with current IDs.
- Default to `WRITE_MODE=dry-run`; only a validated Live run may PATCH `content`, `title`, `tags`, and optional `created`.
- Store no document bytes or full OCR text durably; delete local and Mistral temporary files.

---

## File Structure

```text
src/mistraldock/
  api.py                 # FastAPI routes, auth, lifespan and health/metrics
  config.py              # strict environment settings
  db.py                  # SQLAlchemy engine/session and Alembic integration
  models.py              # database rows and API/domain value objects
  repository.py          # job/run/remote-file transactional persistence
  clients/paperless.py   # API-v10 Paperless HTTP client
  clients/mistral.py     # upload/OCR/annotation/chat/delete adapter
  services/chunking.py   # PDF/image detection, chunk generation and cleanup
  services/metadata.py   # annotation prompts and structured result parsing
  services/validation.py # OCR/title/date/tag and PATCH payload validation
  services/processor.py  # one complete run from Paperless read to result
  services/worker.py     # leases, retries, cleanup and graceful shutdown
tests/
  unit/                  # pure config, persistence, validation and chunk tests
  integration/           # API, worker and provider-contract tests
alembic/                 # schema migration
Dockerfile
compose.example.yml
.env.example
README.md
```

### Task 1: Bootstrap Python package and repeatable test runner

**Files:**

- Create: `pyproject.toml`, `src/mistraldock/__init__.py`, `tests/unit/test_package.py`, `README.md`
- Modify: `.gitignore`

**Interfaces:**

- Produces importable `mistraldock` package and `uv run pytest` command for all later tasks.

- [ ] **Step 1: Write the failing package test**

```python
def test_package_exposes_version() -> None:
    import mistraldock

    assert mistraldock.__version__ == "0.1.0"
```

- [ ] **Step 2: Verify the test fails because the package does not exist**

Run: `uv run --python 3.13 pytest tests/unit/test_package.py -q`

Expected: collection error for missing `mistraldock`.

- [ ] **Step 3: Add PEP 621 project metadata and the minimal package**

```toml
[project]
name = "mistraldock"
version = "0.1.0"
requires-python = ">=3.13,<3.14"
dependencies = ["fastapi", "httpx", "mistralai", "pydantic-settings", "pypdf", "sqlalchemy"]

[dependency-groups]
dev = ["alembic", "pytest", "pytest-asyncio", "respx", "ruff"]
```

```python
__version__ = "0.1.0"
```

- [ ] **Step 4: Verify the unit test and formatter/linter pass**

Run: `uv run --python 3.13 pytest tests/unit/test_package.py -q && uv run --python 3.13 ruff check .`

Expected: 1 passed and zero ruff violations.

- [ ] **Step 5: Commit the bootstrap**

```bash
git add pyproject.toml uv.lock src tests README.md .gitignore
git commit -m "chore: bootstrap Python 3.13 service"
```

### Task 2: Implement strict settings and domain validation

**Files:**

- Create: `src/mistraldock/config.py`, `src/mistraldock/services/validation.py`, `tests/unit/test_config.py`, `tests/unit/test_validation.py`

**Interfaces:**

- Consumes: process environment and Mistral metadata values.
- Produces: `Settings`, `DocumentMetadata`, `ValidatedUpdate`, `validate_title`, `validate_created`, and `resolve_tag_ids`.

- [ ] **Step 1: Write failing configuration tests**

```python
def test_settings_default_to_dry_run(valid_env: dict[str, str]) -> None:
    settings = Settings(_env_file=None, **valid_env)
    assert settings.write_mode is WriteMode.DRY_RUN
    assert settings.ocr_chunk_pages == 8

def test_settings_reject_identical_paperless_and_service_tokens(valid_env: dict[str, str]) -> None:
    valid_env["MISTRALDOCK_API_TOKEN"] = valid_env["PAPERLESS_TOKEN"]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **valid_env)
```

- [ ] **Step 2: Run the new tests and confirm import failures**

Run: `uv run --python 3.13 pytest tests/unit/test_config.py tests/unit/test_validation.py -q`

Expected: collection error for missing modules.

- [ ] **Step 3: Implement settings and pure validators**

```python
class DocumentMetadata(BaseModel):
    title: str
    created: date | None
    tags: list[str]

def build_validated_update(metadata: DocumentMetadata, content: str, current_tag_ids: set[int], tags_by_name: Mapping[str, int], original_filename: str, today: date) -> ValidatedUpdate:
    """Return a PATCH-ready update or raise MetadataValidationError for invalid OCR/title."""
```

Implement the exact title placeholder set from the spec, ISO date range, non-empty OCR checks, case-sensitive tag resolution, warning collection for unknown tags, and a union with `current_tag_ids`.

- [ ] **Step 4: Verify settings and validation tests pass**

Run: `uv run --python 3.13 pytest tests/unit/test_config.py tests/unit/test_validation.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit the domain boundary**

```bash
git add src/mistraldock/config.py src/mistraldock/services/validation.py tests/unit/test_config.py tests/unit/test_validation.py
git commit -m "feat: add strict settings and metadata validation"
```

### Task 3: Add durable job, run, and remote-file persistence

**Files:**

- Create: `src/mistraldock/db.py`, `src/mistraldock/models.py`, `src/mistraldock/repository.py`, `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_initial.py`, `tests/unit/test_repository.py`

**Interfaces:**

- Consumes: `document_id`, trigger kind and timestamps.
- Produces: `JobRepository.enqueue_automatic`, `JobRepository.enqueue_reprocess`, `claim_due_job`, `finish_run`, `schedule_retry`, and `pending_remote_files`.

- [ ] **Step 1: Write failing repository tests for deduplication and leases**

```python
def test_automatic_enqueue_is_idempotent(session: Session) -> None:
    repo = JobRepository(session)
    first = repo.enqueue_automatic(document_id=42, now=NOW)
    second = repo.enqueue_automatic(document_id=42, now=NOW)
    assert second.job_id == first.job_id
    assert repo.count_runs(42) == 0

def test_expired_processing_lease_becomes_retryable(session: Session) -> None:
    repo = JobRepository(session)
    job = repo.enqueue_reprocess(document_id=42, now=NOW)
    repo.claim_due_job(now=NOW, lease_seconds=60)
    assert repo.release_expired_leases(now=NOW + timedelta(seconds=61)) == 1
```

- [ ] **Step 2: Run persistence tests and confirm the repository is absent**

Run: `uv run --python 3.13 pytest tests/unit/test_repository.py -q`

Expected: collection error for missing database modules.

- [ ] **Step 3: Implement the initial SQLite schema and transactional repository**

```python
class JobState(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CONFLICT = "conflict"
```

Add `jobs`, `runs`, and `remote_files` rows. Enforce one active job per `document_id`, record every attempt, persist only hashes/lengths/metadata proposals, and use SQLite WAL/transactions for claims.

- [ ] **Step 4: Run migration and repository tests**

Run: `uv run --python 3.13 alembic upgrade head && uv run --python 3.13 pytest tests/unit/test_repository.py -q`

Expected: migration succeeds and all repository tests pass.

- [ ] **Step 5: Commit persistence**

```bash
git add src/mistraldock/db.py src/mistraldock/models.py src/mistraldock/repository.py alembic.ini alembic tests/unit/test_repository.py
git commit -m "feat: persist jobs runs and cleanup state"
```

### Task 4: Build Paperless/Mistral adapters and chunking

**Files:**

- Create: `src/mistraldock/clients/paperless.py`, `src/mistraldock/clients/mistral.py`, `src/mistraldock/services/chunking.py`, `src/mistraldock/services/metadata.py`, `tests/unit/test_chunking.py`, `tests/integration/test_paperless_client.py`, `tests/integration/test_mistral_client.py`

**Interfaces:**

- Produces `PaperlessClient.get_document`, `download_original`, `list_tags`, `patch_document`; `MistralClient.ocr_chunk`, `consolidate_metadata`, `delete_file`; and `chunk_document`.

- [ ] **Step 1: Write failing tests for API headers, pagination and chunk boundaries**

```python
@respx.mock
async def test_paperless_lists_all_tag_pages() -> None:
    respx.get("https://paperless.example/api/tags/").respond(200, json={"results": [{"id": 1, "name": "Invoice"}], "next": "https://paperless.example/api/tags/?page=2"})
    respx.get("https://paperless.example/api/tags/?page=2").respond(200, json={"results": [{"id": 2, "name": "Tax"}], "next": None})
    assert await client.list_tags() == {"Invoice": 1, "Tax": 2}

def test_pdf_is_split_into_eight_page_chunks(tmp_path: Path) -> None:
    assert [chunk.page_numbers for chunk in chunk_document(nine_page_pdf)] == [tuple(range(8)), (8,)]
```

- [ ] **Step 2: Run the adapter/chunk tests and confirm they fail due to missing modules**

Run: `uv run --python 3.13 pytest tests/unit/test_chunking.py tests/integration/test_paperless_client.py tests/integration/test_mistral_client.py -q`

Expected: collection error for missing adapters.

- [ ] **Step 3: Implement external adapters and lifecycle-safe chunking**

```python
async def patch_document(self, document_id: int, version_id: int, payload: dict[str, object]) -> None:
    await self._client.patch(f"/api/documents/{document_id}/", params={"version": version_id}, json=payload)

async def ocr_chunk(self, chunk: DocumentChunk, vocabulary: list[str]) -> OCRChunkResult:
    """Upload, OCR with document annotation, and return Markdown + three-field metadata."""
```

Use `original=true`, API-v10 Accept header, full pagination, an eight-page chunk maximum, Mistral upload/signed URL/OCR/delete in `finally`, annotation JSON schema, and a temperature-zero structured chat consolidation for multi-chunk documents.

- [ ] **Step 4: Verify adapter/chunk tests pass**

Run: `uv run --python 3.13 pytest tests/unit/test_chunking.py tests/integration/test_paperless_client.py tests/integration/test_mistral_client.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit provider adapters**

```bash
git add src/mistraldock/clients src/mistraldock/services/chunking.py src/mistraldock/services/metadata.py tests/unit/test_chunking.py tests/integration/test_paperless_client.py tests/integration/test_mistral_client.py
git commit -m "feat: add Paperless and Mistral document adapters"
```

### Task 5: Orchestrate complete processing, retries and cleanup

**Files:**

- Create: `src/mistraldock/services/processor.py`, `src/mistraldock/services/worker.py`, `tests/integration/test_processor.py`, `tests/integration/test_worker.py`

**Interfaces:**

- Consumes claimed jobs and dependency-injected repository/clients.
- Produces `DocumentProcessor.process(job)` and `Worker.run_once(now)`.

- [ ] **Step 1: Write failing end-to-end processor tests**

```python
async def test_processor_applies_one_versioned_patch_in_live_mode(fake_dependencies: Dependencies) -> None:
    result = await DocumentProcessor(fake_dependencies).process(Job(document_id=42))
    assert result.state is JobState.SUCCEEDED
    assert fake_dependencies.paperless.patch_payloads == [{"content": "OCR", "title": "Vodafone – Kabelrechnung Juli 2026", "created": "2026-07-01", "tags": [4, 9]}]

async def test_processor_returns_conflict_without_patch_when_document_changed(fake_dependencies: Dependencies) -> None:
    result = await DocumentProcessor(fake_dependencies).process(Job(document_id=42))
    assert result.state is JobState.CONFLICT
    assert fake_dependencies.paperless.patch_payloads == []
```

- [ ] **Step 2: Verify the processor tests fail because no orchestrator exists**

Run: `uv run --python 3.13 pytest tests/integration/test_processor.py tests/integration/test_worker.py -q`

Expected: collection error for missing processor/worker.

- [ ] **Step 3: Implement processor and worker state transitions**

```python
class DocumentProcessor:
    async def process(self, job: ClaimedJob) -> ProcessResult: ...

class Worker:
    async def run_once(self, now: datetime) -> bool:
        """Claim one due job, process it, and save success/retry/failure/conflict."""
```

Refresh document and tags before update, reject changed versions/timestamps, use the exact retry formula with jitter, verify an ambiguous PATCH timeout by GET, run Mistral cleanup retries, and set `applied=False` in dry-run mode.

- [ ] **Step 4: Verify processor and worker behavior**

Run: `uv run --python 3.13 pytest tests/integration/test_processor.py tests/integration/test_worker.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit orchestration**

```bash
git add src/mistraldock/services/processor.py src/mistraldock/services/worker.py tests/integration/test_processor.py tests/integration/test_worker.py
git commit -m "feat: process queued Paperless documents safely"
```

### Task 6: Expose FastAPI, operational endpoints and secure manual processing

**Files:**

- Create: `src/mistraldock/api.py`, `tests/integration/test_api.py`

**Interfaces:**

- Produces `create_app(settings: Settings) -> FastAPI` with the five endpoints in the specification.

- [ ] **Step 1: Write failing HTTP API tests**

```python
async def test_webhook_queues_document_and_returns_202(client: AsyncClient) -> None:
    response = await client.post("/v1/webhooks/paperless", headers=auth_header(), json={"document_id": 42})
    assert response.status_code == 202
    assert response.json()["state"] == "queued"

async def test_reprocess_requires_bearer_token(client: AsyncClient) -> None:
    assert (await client.post("/v1/documents/42/reprocess")).status_code == 401
```

- [ ] **Step 2: Run API tests and confirm they fail due to the missing app factory**

Run: `uv run --python 3.13 pytest tests/integration/test_api.py -q`

Expected: collection error for missing `create_app`.

- [ ] **Step 3: Implement routes, lifespan and bounded metrics**

```python
def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="MistralDock", version=__version__)
    # POST /v1/webhooks/paperless
    # POST /v1/documents/{document_id}/reprocess
    # GET /v1/documents/{document_id}/runs
    # GET /health/live, /health/ready, /metrics
    return app
```

Use constant-time bearer-token comparison, start one background worker in lifespan, return 202 before processing, and exclude document content/name/title/tag values from logs/metrics.

- [ ] **Step 4: Verify API tests pass**

Run: `uv run --python 3.13 pytest tests/integration/test_api.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit the HTTP service**

```bash
git add src/mistraldock/api.py tests/integration/test_api.py
git commit -m "feat: expose authenticated MistralDock API"
```

### Task 7: Package, document, verify and conduct Paperless read-only smoke test

**Files:**

- Create: `Dockerfile`, `compose.example.yml`, `.env.example`, `tests/integration/test_container_config.py`
- Modify: `README.md`

**Interfaces:**

- Produces a non-root container with writable `/data` and `/tmp`, no Paperless volume, plus deployment and Paperless workflow instructions.

- [ ] **Step 1: Write failing deployment/configuration tests**

```python
def test_example_compose_has_no_paperless_volume_mount() -> None:
    compose = yaml.safe_load(Path("compose.example.yml").read_text())
    assert compose["services"]["mistraldock"].get("volumes") == ["mistraldock-data:/data"]

def test_env_example_defaults_to_dry_run() -> None:
    assert "WRITE_MODE=dry-run" in Path(".env.example").read_text()
```

- [ ] **Step 2: Run deployment tests and verify they fail because files are absent**

Run: `uv run --python 3.13 pytest tests/integration/test_container_config.py -q`

Expected: failure for missing Compose/environment files.

- [ ] **Step 3: Add Docker/Compose/docs and local secret template**

```yaml
services:
  mistraldock:
    build: .
    env_file: .env
    volumes:
      - mistraldock-data:/data
    read_only: true
    tmpfs: [/tmp]
```

Document: Paperless service-account permissions, exact Document Added webhook body/header, initial dry-run evaluation of 20–50 documents, reprocess API, promotion to live mode, and rollback.

- [ ] **Step 4: Verify all automated tests, lint and container build**

Run: `uv run --python 3.13 pytest -q && uv run --python 3.13 ruff check . && docker build -t mistraldock:local .`

Expected: all tests pass, zero ruff errors and a successful image build.

- [ ] **Step 5: Run the user-authorized read-only Paperless smoke test**

Run: `curl --fail-with-body --silent --show-error -H "Authorization: Token $PAPERLESS_TOKEN" -H "Accept: application/json; version=10" "$PAPERLESS_URL/api/tags/?page_size=1"`

Expected: HTTP 200 and a paginated tag response; never print or persist the token.

- [ ] **Step 6: Commit packaging and documentation**

```bash
git add Dockerfile compose.example.yml .env.example README.md tests/integration/test_container_config.py uv.lock
git commit -m "docs: package and operate MistralDock"
```

## Final Verification

- [ ] Re-read the specification and map every v1 requirement to Tasks 1–7.
- [ ] Run `uv run --python 3.13 pytest -q`, `uv run --python 3.13 ruff check .`, and `docker build -t mistraldock:local .` with fresh output.
- [ ] Run `git diff main...HEAD --check` and inspect `git status --short` for an expected clean worktree.
- [ ] Confirm the Paperless smoke test uses token auth/API v10 and performs no write operation.
- [ ] Report the committed implementation, outstanding requirement for a Mistral API key, and exact deployment commands without exposing any secret.

