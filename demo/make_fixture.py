#!/usr/bin/env python3
"""Generate demo/fixture.zip deterministically.

The fixture is a small repository that exercises every metric edge case:

- two authors; Alice appears under two distinct email identities (for the
  manual-merge journey)
- initial adds across src/, docs/ and the repository root
- edits to two files in one commit
- a pure rename (zero metric impact)
- a rename + edit (attributed to the new path)
- a deletion (removed lines counted)
- a binary file (PNG) that must be excluded from metrics
- a --no-ff merge commit that must be excluded from metrics
- fixed author/committer timestamps spread over Jan-Apr 2026 (+02:00)

The zip contains a single top-level folder holding .git, matching the
"single top-level folder" ingestion path.

Run:  python3 demo/make_fixture.py
"""

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_ZIP = HERE / "fixture.zip"
TOP = "rat-fixture"

ALICE = ("Alice Adams", "alice@example.com")
ALICE2 = ("Alice A.", "alice.alt@example.com")
BOB = ("Bob Brown", "bob@example.com")

D1 = "2026-01-10T10:00:00+02:00"
D2 = "2026-01-20T09:30:00+02:00"
D3 = "2026-02-05T14:00:00+02:00"
D4 = "2026-02-15T16:45:00+02:00"
D5 = "2026-03-01T11:15:00+02:00"
D6 = "2026-03-10T10:00:00+02:00"
D7 = "2026-03-20T10:00:00+02:00"
D8 = "2026-03-25T10:00:00+02:00"
D9 = "2026-03-26T18:00:00+02:00"
D10 = "2026-04-01T15:00:00+02:00"

README_C1 = "# RAT Fixture\n\nA small repository for testing RAT metrics.\n"
MAIN_C1 = (
    "import sys\n"
    "\n"
    "from util import add\n"
    "\n"
    "\n"
    "def main():\n"
    "    print(add(1, 2))\n"
    "    return 0\n"
)
MAIN_C2 = (
    "import os\n"
    "import json\n"
    "\n"
    "from util import add\n"
    "\n"
    "\n"
    "def main():\n"
    "    print(add(1, 2))\n"
    "    return 0\n"
)
UTIL_C1 = (
    "def add(a, b):\n"
    "    return a + b\n"
    "\n"
    "\n"
    "def sub(a, b):\n"
    "    return a - b\n"
)
HELPERS_C4 = (
    "def add(a, b):\n"
    "    return a + b  # sum\n"
    "\n"
    "\n"
    "def sub(a, b):\n"
    "    return a - b\n"
)
GUIDE_C1 = "# Guide\n\nRead this before changing src/.\n"
GUIDE_C2 = "# Guide\n\nRead this before changing anything in src/.\n"
FEATURE_C7 = (
    "def feature():\n"
    '    return "working"\n'
    "\n"
    "\n"
    "FLAG = True\n"
    "\n"
    "\n"
    "ENABLED = 1\n"
)
FEATURE_C10 = (
    "def feature():\n"
    '    return "working"\n'
    "\n"
    "\n"
    "FLAG = True\n"
    "\n"
    "\n"
    "ENABLED = 2\n"
    "VERSION = 2\n"
)


def tiny_png():
    """A deterministic 4x4 amber PNG (closes over zlib/struct - no deps)."""

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    width = height = 4
    raw = b"".join(b"\x00" + bytes([180, 83, 9] * width) for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _commit_env(author, date):
    name, email = author
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
            "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_DATE": date,
        }
    )
    return env


def git(repo, *args, author=None, date=None):
    env = _commit_env(author, date) if author else dict(os.environ)
    proc = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-C", str(repo)] + list(args),
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        raise SystemExit("git %s failed" % " ".join(args))
    return proc.stdout.strip()


def write(repo, rel, content):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def build_zip(repo, out):
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(repo.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(repo).as_posix()
            info = zipfile.ZipInfo("%s/%s" % (TOP, rel), date_time=(2026, 4, 2, 12, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100644 & 0xFFFF) << 16
            zf.writestr(info, path.read_bytes())


def main():
    tmp = Path(tempfile.mkdtemp(prefix="rat-fixture-build-"))
    try:
        repo = tmp / TOP
        repo.mkdir()
        git(repo, "init", "-q")

        # c1 - initial adds
        write(repo, "README.md", README_C1)
        write(repo, "src/main.py", MAIN_C1)
        write(repo, "src/util.py", UTIL_C1)
        write(repo, "docs/guide.md", GUIDE_C1)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "Initial commit", author=ALICE, date=D1)
        base_branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")

        # c2 - edits to two files
        write(repo, "src/main.py", MAIN_C2)
        write(repo, "docs/guide.md", GUIDE_C2)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "Tidy imports and docs", author=BOB, date=D2)

        # c3 - pure rename
        git(repo, "mv", "src/util.py", "src/helpers.py")
        git(repo, "commit", "-q", "-m", "Rename util module to helpers", author=ALICE, date=D3)

        # c4 - rename + edit, by Alice's second identity
        write(repo, "src/helpers.py", HELPERS_C4)
        (repo / "lib").mkdir(exist_ok=True)
        git(repo, "mv", "src/helpers.py", "lib/helpers.py")
        git(repo, "add", "-A")
        git(
            repo,
            "commit",
            "-q",
            "-m",
            "Move helpers to lib and annotate add",
            author=ALICE2,
            date=D4,
        )

        # c5 - deletion
        git(repo, "rm", "-q", "docs/guide.md")
        git(repo, "commit", "-q", "-m", "Remove obsolete guide", author=BOB, date=D5)

        # c6 - binary file add
        write(repo, "assets/logo.png", tiny_png())
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "Add logo asset", author=ALICE, date=D6)

        # c7 - feature branch commit
        git(repo, "checkout", "-q", "-b", "feature")
        write(repo, "src/feature.py", FEATURE_C7)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "Start feature module", author=ALICE2, date=D7)

        # c8 - main advances
        git(repo, "checkout", "-q", base_branch)
        write(repo, "README.md", README_C1 + "Second line of the readme.\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "Touch up readme", author=BOB, date=D8)

        # c9 - merge commit that must be excluded
        git(repo, "merge", "--no-ff", "-q", "-m", "Merge branch 'feature'", "feature", author=ALICE, date=D9)

        # c10 - edit the feature module
        write(repo, "src/feature.py", FEATURE_C10)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "Polish feature module", author=ALICE, date=D10)

        build_zip(repo, OUT_ZIP)
        size = OUT_ZIP.stat().st_size
        print("wrote %s (%d bytes)" % (OUT_ZIP, size))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
