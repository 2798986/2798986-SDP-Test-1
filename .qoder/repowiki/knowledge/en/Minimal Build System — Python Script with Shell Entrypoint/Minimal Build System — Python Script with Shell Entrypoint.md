---
kind: build_system
name: Minimal Build System — Python Script with Shell Entrypoint
category: build_system
scope:
    - '**'
source_files:
    - start.sh
    - app.py
    - selftest.py
---

## What system/approach is used

This repository has **no formal build system**. It is a single-package Python application (`app.py`, `db.py`, `ingest.py`, `metrics.py`, `selftest.py`) that runs directly via the CPython interpreter. There is no Makefile, Dockerfile, CI pipeline, `setup.py`, `pyproject.toml`, `requirements.txt`, or `.github/` workflows in the repository.

The only executable entrypoint is `start.sh`, a thin shell wrapper that changes into the script's own directory and execs `python3 app.py`. The static frontend under `static/` (vanilla JS + CSS) is served as-is by the Python HTTP server; there is no asset compilation step.

## Key files

- `start.sh` — sole entrypoint; sets `-e`, `cd`s to the script's directory, then `exec python3 app.py`.
- `app.py` — web server / analysis engine entrypoint (imported by `start.sh`).
- `selftest.py` — built-in self-test module (not invoked from any build script).
- `static/index.html`, `static/app.js`, `static/style.css` — frontend assets served verbatim.

## Architecture and conventions

- **No dependency manifest**: all imports are resolved against whatever Python environment is active at runtime. No virtualenv activation, pip install, or lock file is configured.
- **No packaging**: there is no wheel/sdist distribution target; the repo is intended to be run directly from source.
- **No containerization**: no Dockerfile or docker-compose exists.
- **No CI**: no GitHub Actions, Travis, CircleCI, or similar configuration is present.
- **Static assets are not built**: the frontend is plain HTML/CSS/JS served directly by the Python server.

## Conventions and constraints

- The process must be launched through `start.sh` (or an equivalent command that `cd`s into the repo root before invoking `python3 app.py`), because `start.sh` explicitly `cd`s to its own directory — this is enforced by the script itself.
- The script uses `set -e`, so any non-zero exit from `python3 app.py` will terminate the wrapper.
- The runtime requires a `python3` binary on `$PATH`; no version pinning is declared anywhere in the repo.