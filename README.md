# RAT - Repo Analysis Tool

A local, offline dashboard that ingests Git repositories (zip containing `.git`, or a public
clone URL) and computes added/removed lines, growth, churn, modifications, churn rate and
ownership for every file, directory, repository, commit set and author.

Developed with Qoder, an agentic coding tool, as permitted by the test instructions.

## Requirements

- Python 3.8+ (standard library only - no pip, no venv, no build step)
- Git (for cloning and history extraction)

## Run

```
python3 app.py
```

Then open the printed URL (default `http://127.0.0.1:8000`). `./start.sh` is an equivalent
convenience wrapper.

## What to try first

1. Click **Load sample** - ingests the bundled fixture repository (`demo/fixture.zip`, fully offline).
2. Click **Add repository** and paste `https://github.com/DaveGamble/cJSON.git` for a real-world repo.

## Optional environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `8000` | First port tried; falls back to 8080, 8888, 9000 if busy |
| `HOST` | `127.0.0.1` | Bind address |
| `DATA_DIR` | `<repo>/data` | Where the SQLite database and ingested repos live |

## Troubleshooting

- Occupied ports fall back automatically - the actual URL is always printed at startup.
- Ensure `git` is on your `PATH` (needed for cloning and history extraction).
- Reset everything by deleting the `data/` directory.

## Development

- `python3 selftest.py` - asserts the metric engine's edge cases against the fixture.
- `python3 demo/make_fixture.py` - regenerates `demo/fixture.zip` deterministically.
