---
kind: error_handling
name: Exception-Based Error Handling with Layered Domain Errors
category: error_handling
scope:
    - '**'
source_files:
    - app.py
    - ingest.py
    - metrics.py
---

## Approach

RAT uses Python's built-in exception hierarchy — no third-party error library, no HTTP middleware framework. Errors are raised as domain-specific `Exception` subclasses and converted to JSON HTTP responses at the request boundary.

There are three layers of error types:

1. **HTTP-layer sentinel**: `ApiError(Exception)` in `app.py` (line 47) carries a numeric `status` code and a user-facing `message`. It is the only type caught by the top-level `_safe` wrapper (`RatHandler._safe`, line 73–90), which turns it into `{"error": ...}` JSON with the given status.
2. **Domain-layer errors** raised by business logic:
   - `metrics.FilterError(ValueError)` — bad or out-of-range query parameters (e.g. invalid hash, path too long, unknown chart type). Comment on line 30 says "Bad or out-of-range input; surfaced as JSON 400."
   - `metrics.NotFound(LookupError)` — entity lookup failures (e.g. repository not found).
   - `ingest.IngestError(Exception)` — ingestion/validation failures (invalid URL, bad zip, git command failure, missing `.git`).
3. **Transport/connection errors**: `BrokenPipeError`, `ConnectionResetError`, and `OSError` for port binding are handled explicitly and silently swallowed where appropriate.

## Request Boundary: Centralized Try/Except

Every HTTP verb (`do_GET`, `do_HEAD`, `do_POST`, `do_DELETE`) funnels through `RatHandler._safe(method)` (line 73). The wrapper does three things:
- Catches `ApiError` → responds with its `status` and `message` as JSON.
- Catches `(BrokenPipeError, ConnectionResetError)` → silently ignores client disconnects.
- Catches bare `Exception` → prints traceback via `traceback.print_exc()` and returns `{"error": "Internal server error"}` with status 500.

This is the single point that prevents unhandled exceptions from leaking raw tracebacks to clients.

## Mapping Domain Errors to HTTP Status Codes

API handlers translate domain exceptions into `ApiError` calls with explicit status codes:

| Domain exception | HTTP status | Where |
|---|---:|---|
| `metrics.NotFound` | 404 | `_metrics`, `_commits`, `_merge_authors` (lines 205–208, 235–238, 254–257) |
| `metrics.FilterError` | 400 | Same locations |
| Invalid integer / out-of-range params | 400 | `_commits.limit_of` (line 220–222), `_read_json`, `_read_body` |
| Unknown endpoint | 404 | `_api` fallback (line 196) |
| Missing static file / path traversal | 404 | `_static` (lines 432–437) |
| Bad upload content-type check | 400 | `_create_repo_from_upload` (line 357) |
| Payload too large | 413 | `_read_body` (line 411) |
| Missing Content-Length | 411 | `_read_body` (line 404) |
| Missing demo fixture | 500 | `_create_sample_repo` (line 379) |

The mapping is consistent: validation/input errors → 400, not-found → 404, unexpected server state → 500.

## Background Job Error Handling

Ingestion runs in daemon threads started from `_start_ingest` (line 330). `run_ingest` (line 356) has its own try/except layer:
- `IngestError` → routed through `_fail(conn, job_id, repo_id, message)` (line 427–428), which deletes partially ingested rows, sets `repos.status = 'error'` and `jobs.status = 'error'` with the message.
- Bare `Exception` → same `_fail` path but prefixed with `"Unexpected error: %s"` (line 429–431).
- `finally` block closes the DB connection and removes temporary payload files if the job failed.

`_fail` itself wraps its DB writes in a try/except that prints a traceback and swallows the exception, ensuring the failure-reporting path cannot crash the worker thread.

Startup-time recovery is handled by `recover_stale_jobs()` (line 443–457): any jobs still marked `'running'` are reset to `'error'` with message `"Interrupted by server restart"`, and repos in `'pending'`/`'running'` are similarly marked errored. This is the process's recovery mechanism since background threads do not survive restarts.

## Validation Strategy

Validation is done inline at call sites rather than centralized validators:
- URL validation: `ingest.validate_url(url)` raises `IngestError` for empty or non-http(s) URLs (line 70–78).
- Zip safety: `extract_zip` checks for `..` segments and resolves paths against the destination root to prevent zip-slip attacks (lines 113–118).
- Path normalization: `metrics.clean_path` rejects `.`/`..` segments and enforces max length (lines 81–88).
- Integer bounds: `_parse_int` in metrics and `limit_of` in app both enforce `[lo, hi]` ranges and raise `FilterError` / `ApiError(400, ...)` respectively.

## Conventions Observed

- Business logic modules (`metrics.py`, `ingest.py`) raise their own domain exceptions and never return HTTP status codes.
- The web layer (`app.py`) is the sole place that converts domain exceptions to `ApiError` instances with HTTP status codes.
- All DB connections are wrapped in `try/finally conn.close()` blocks around handler methods.
- Background jobs use a dedicated `_fail` helper instead of raising exceptions directly, so partial-state cleanup is guaranteed.
- Client-disconnect errors (`BrokenPipeError`, `ConnectionResetError`) are treated as non-errors and silently ignored at both the request wrapper and response writer levels.
- There is no `panic`/`recover` equivalent beyond the broad `except Exception` catch-all in `_safe` and `run_ingest`, which serve as last-resort safety nets.