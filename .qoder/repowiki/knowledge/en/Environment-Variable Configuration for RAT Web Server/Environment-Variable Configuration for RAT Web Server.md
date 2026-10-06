---
kind: configuration_system
name: Environment-Variable Configuration for RAT Web Server
category: configuration_system
scope:
    - '**'
source_files:
    - app.py
    - db.py
    - start.sh
    - selftest.py
---

## Approach

RAT has no configuration framework, config files, or feature-flag system. Runtime settings are read exclusively from **process environment variables** via `os.environ.get(...)`, with all values falling back to hard-coded defaults. The project is a single-process Python application (`app.py`) built on the stdlib `http.server` module; there is no `config/` directory, no `.env` loader, no YAML/TOML/JSON config file.

## Environment Variables

Three variables are consumed at startup:

| Variable | Default | Source | Behavior |
|---|---:|---|---|
| `PORT` | `8000` | `app.py:_port_candidates()` (line 445) | First port to try; validated as an integer in `[0, 65535]`. If unavailable, falls back through `(8080, 8888, 9000, 0)` where `0` lets the OS pick a free port. |
| `HOST` | `127.0.0.1` | `app.py:main()` (line 458) | Bind address; empty string is coerced back to the default. |
| `DATA_DIR` | `<repo>/data` | `db.py:data_dir()` (line 67) | Absolute path to the SQLite + repo storage root; expanded via `Path.expanduser().resolve()`. All filesystem paths in the app are anchored here rather than the process working directory. |

No other environment variables are read by production code. `ingest.py` and `demo/make_fixture.py` pass `dict(os.environ)` into subprocesses so that git credentials / proxy env vars propagate, but they do not define new RAT settings.

## Startup Flow

`start.sh` simply `cd`s to the script's directory and execs `python3 app.py`; it performs no env parsing itself. `app.py.main()` calls `db.init_db()` first (which creates `DATA_DIR` if missing), then reads `HOST`/`PORT`, binds the server, and prints the resolved `DATA_DIR` path to stdout.

## Conventions Observed

- Every configurable value is a **single uppercase env var** read directly from `os.environ.get(name, "default")` — no indirection layer, no validation library, no schema.
- Defaults are documented in the module docstring of `app.py` (lines 6–10) and mirrored inline near each read site.
- There is **no precedence rule** between multiple sources (e.g. no config-file fallback); environment is the sole source of truth.
- Secrets are not handled specially — any secret would be passed through the same env-var mechanism.
- Tests override `DATA_DIR` by mutating `os.environ` directly (`selftest.py` lines 59, 98), confirming that env-based configuration is the intended extension point.

## Constraints Enforced by Code

- `PORT` must be a decimal integer in `[0, 65535]`; otherwise the default `8000` is used (`app.py` line 446).
- `DATA_DIR` is resolved through `pathlib.Path.expanduser().resolve()`, so `~` and relative paths are normalized before use (`db.py` line 69).
- `DATA_DIR` is created lazily with `mkdir(parents=True, exist_ok=True)` inside `connect()` (`db.py` line 80), so the directory does not need to pre-exist.
- `HOST` is stripped of whitespace; an empty value reverts to `"127.0.0.1"` (`app.py` line 458).

## Key Files

- `app.py` — server entrypoint, reads `PORT` and `HOST`.
- `db.py` — reads `DATA_DIR`, defines `data_dir()` / `repos_dir()` helpers used throughout the app.
- `start.sh` — thin launcher, no config logic.
- `selftest.py` — demonstrates test-time override of `DATA_DIR`.