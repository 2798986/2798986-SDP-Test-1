"""RAT - ingestion pipeline.

Zip extraction (zip-slip safe), URL cloning, and a single-pass git-history
extraction into SQLite.  Ingest jobs run in background threads and publish
progress into the jobs table.

Verified byte layout of `git log --no-merges --use-mailmap -M50% --numstat -z
--pretty=format:...` (git 2.43, confirmed by hexdump of the fixture repo):

- Records are NUL separated.
- A commit-header token starts with \\x01 and carries
  `hash \\x1f author-name \\x1f author-email \\x1f committer-ts \\x1f subject`
  immediately followed by `\\n` and the commit's FIRST file record inside the
  same token.
- A rename record leaves its path field empty (`ADDED\\tREMOVED\\t`) and the
  next two NUL tokens carry the old and the new path; the change is
  attributed to the NEW path.
- `-` instead of digits marks a binary file: never measured.
- Empty tokens separate commits.
"""

import os
import shutil
import subprocess
import traceback
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import db

HISTORY_FORMAT = "%x01%H%x1f%aN%x1f%aE%x1f%ct%x1f%s"
BATCH_ROWS = 5000
PROGRESS_EVERY = 200
MAX_CLONE_SECONDS = 1800


class IngestError(Exception):
    """A user-readable ingestion failure."""


# ------------------------------------------------------------- subprocess

def _git_env():
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # never block on an auth prompt
    return env


def run_git(cwd, args, timeout=None):
    """Run git with an argument list (never through a shell)."""
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=false"] + list(args),
            cwd=str(cwd),
            env=_git_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise IngestError("git %s timed out" % args[0])
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        line = stderr.splitlines()[-1] if stderr else ""
        raise IngestError(line or ("git %s failed" % args[0]))
    return proc


def validate_url(url):
    if not url:
        raise IngestError("A repository URL is required.")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise IngestError(
            "Enter a valid public clone URL starting with http:// or https://"
        )
    return url


def name_from_url(url):
    path = urlparse(url).path.rstrip("/")
    name = path.rsplit("/", 1)[-1] if path else ""
    if name.endswith(".git"):
        name = name[:-4]
    return name or "repository"


# ------------------------------------------------------------- extraction

def extract_zip(zip_path, dest):
    """Extract a repository archive (zip-slip safe).

    Returns the repository root: the directory holding `.git` (.git may be a
    directory or a gitdir-pointer file).  Accepts the repository at the
    archive root or inside a single top-level folder.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    try:
        zf = zipfile.ZipFile(str(zip_path))
    except (zipfile.BadZipFile, OSError):
        raise IngestError("The file is not a valid zip archive.")
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            parts = [p for p in name.split("/") if p not in ("", ".")]
            if not parts:
                continue
            if ".." in parts:
                raise IngestError("Zip contains an unsafe path: %s" % info.filename)
            target = dest.joinpath(*parts)
            resolved = target.resolve()
            if resolved != dest_resolved and dest_resolved not in resolved.parents:
                raise IngestError("Zip contains an unsafe path: %s" % info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(str(target), "wb") as dst:
                shutil.copyfileobj(src, dst)
    return find_repo_root(dest)


def find_repo_root(dest):
    dest = Path(dest)
    if (dest / ".git").exists():
        return dest
    tops = sorted(
        p for p in dest.iterdir() if p.is_dir() and p.name != "__MACOSX"
    )
    for top in tops:
        if (top / ".git").exists():
            return top
    # one more level down (nested single-folder archives)
    for top in tops:
        for sub in sorted(p for p in top.iterdir() if p.is_dir()):
            if (sub / ".git").exists():
                return sub
    raise IngestError(
        "No .git directory found in the archive - RAT needs the repository's "
        "full Git history."
    )


def head_hash(repo_root):
    proc = run_git(repo_root, ["rev-parse", "HEAD"])
    return proc.stdout.decode("utf-8", "replace").strip()


def _rev_count(repo_root):
    try:
        proc = run_git(repo_root, ["rev-list", "--no-merges", "--count", "HEAD"], timeout=300)
        return int(proc.stdout.decode("utf-8", "replace").strip() or "0")
    except (IngestError, ValueError):
        return 0


def _iter_tokens(stream):
    """Yield NUL-separated tokens from a binary stream (chunked)."""
    buf = b""
    while True:
        chunk = stream.read(1 << 20)
        if not chunk:
            break
        buf += chunk
        parts = buf.split(b"\x00")
        buf = parts.pop()
        for part in parts:
            yield part
    if buf:
        yield buf


def _decode(raw):
    return raw.decode("utf-8", "replace")


# -------------------------------------------------------------- history

def read_history(repo_root, conn, repo_id, progress=None):
    """Parse the full history in one pass into commits/authors/files rows.

    ``progress(fraction, message)`` is called throttled while reading.
    Returns a stats dict.
    """
    total = _rev_count(repo_root)
    cmd = [
        "git", "-c", "core.quotepath=false",
        "log", "--no-merges", "--use-mailmap", "-M50%",
        "--numstat", "-z", "--pretty=format:" + HISTORY_FORMAT,
    ]
    proc = subprocess.Popen(
        cmd, cwd=str(repo_root), env=_git_env(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    file_batch = []
    stats = {"commits": 0, "rows": 0, "renames": 0, "binaries": 0}
    author_ids = {}
    current_commit = None
    rename_state = None  # [commit_id, is_binary, added, removed, old_path]

    insert_files_sql = (
        "INSERT INTO files(repo_id, commit_id, path, added, removed) "
        "VALUES (?, ?, ?, ?, ?)"
    )

    def flush_files():
        if file_batch:
            conn.executemany(insert_files_sql, file_batch)
            file_batch.clear()
            conn.commit()

    def add_row(commit_id, path, added, removed):
        file_batch.append((repo_id, commit_id, path, added, removed))
        stats["rows"] += 1
        if len(file_batch) >= BATCH_ROWS:
            flush_files()

    def resolve_author(name, email):
        key = (name, email)
        aid = author_ids.get(key)
        if aid is None:
            conn.execute(
                "INSERT OR IGNORE INTO authors(repo_id, name, email, canonical_id) "
                "VALUES (?, ?, ?, NULL)",
                (repo_id, name, email),
            )
            row = conn.execute(
                "SELECT id FROM authors WHERE repo_id = ? AND name = ? AND email = ?",
                (repo_id, name, email),
            ).fetchone()
            aid = row[0]
            author_ids[key] = aid
        return aid

    def handle_file_record(raw):
        nonlocal rename_state
        parts = raw.split(b"\t")
        if len(parts) < 3:
            return
        added_s, removed_s = parts[0], parts[1]
        path = b"\t".join(parts[2:])  # file names may contain tabs
        is_binary = added_s == b"-"
        added = removed = 0
        if not is_binary:
            try:
                added, removed = int(added_s), int(removed_s)
            except ValueError:
                return
        else:
            stats["binaries"] += 1
        if path == b"":
            # Rename: the next two tokens carry the old and the new path.
            rename_state = [current_commit, is_binary, added, removed, None]
            return
        if is_binary or current_commit is None:
            return
        add_row(current_commit, _decode(path), added, removed)

    try:
        for tok in _iter_tokens(proc.stdout):
            if not tok:
                continue
            if tok[0:1] == b"\x01":
                fields = tok[1:].split(b"\x1f", 4)
                if len(fields) != 5:
                    continue
                hash_b, name_b, email_b, ts_b, rest = fields
                subject_b, sep, first_record = rest.partition(b"\n")
                try:
                    ts = int(ts_b)
                except ValueError:
                    continue
                author_id = resolve_author(_decode(name_b), _decode(email_b))
                cur = conn.execute(
                    "INSERT INTO commits(repo_id, hash, author_id, ts, subject) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (repo_id, _decode(hash_b), author_id, ts, _decode(subject_b)),
                )
                current_commit = cur.lastrowid
                stats["commits"] += 1
                if sep and first_record:
                    handle_file_record(first_record)
                if progress and total and stats["commits"] % PROGRESS_EVERY == 0:
                    frac = min(stats["commits"] / float(total), 1.0)
                    progress(frac, "Reading history - %d of %d commits"
                             % (stats["commits"], total))
            elif rename_state is not None:
                if rename_state[4] is None:
                    rename_state[4] = tok  # old path consumed
                else:
                    commit_id, is_binary, added, removed, _old = rename_state
                    if not is_binary and commit_id is not None:
                        add_row(commit_id, _decode(tok), added, removed)
                        stats["renames"] += 1
                    rename_state = None
            else:
                handle_file_record(tok)
        flush_files()
        proc.wait(timeout=120)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()
        stderr_raw = proc.stderr.read()
        proc.stderr.close()
    if proc.returncode != 0:
        stderr = stderr_raw.decode("utf-8", "replace").strip()
        raise IngestError(stderr.splitlines()[-1] if stderr else "git log failed")
    if progress:
        progress(1.0, "Reading history - %d commits" % stats["commits"])
    return stats


# ------------------------------------------------------------- job thread

def _progress(conn, job_id, repo_id, frac, message):
    conn.execute(
        "UPDATE jobs SET progress = ?, message = ? WHERE id = ?",
        (float(frac), message, job_id),
    )
    conn.execute(
        "UPDATE repos SET status = 'running' WHERE id = ? AND status != 'running'",
        (repo_id,),
    )
    conn.commit()


def _fail(conn, job_id, repo_id, message):
    """Mark the job and repo failed and drop any partially ingested rows."""
    try:
        conn.execute("DELETE FROM files WHERE repo_id = ?", (repo_id,))
        conn.execute("DELETE FROM commits WHERE repo_id = ?", (repo_id,))
        conn.execute("DELETE FROM authors WHERE repo_id = ?", (repo_id,))
        conn.execute(
            "UPDATE repos SET status = 'error', error = ? WHERE id = ?",
            (message, repo_id),
        )
        conn.execute(
            "UPDATE jobs SET status = 'error', progress = 1.0, message = ? WHERE id = ?",
            (message, job_id),
        )
        conn.commit()
    except Exception:
        traceback.print_exc()


def _is_inside(child, parent):
    c, p = Path(child).resolve(), Path(parent).resolve()
    return c == p or p in c.parents


def run_ingest(job_id, repo_id, source, payload):
    """Background thread target.

    source 'url'             payload = clone URL
    source 'zip' | 'sample'  payload = path to a zip archive
    """
    conn = db.connect()
    dest = db.data_dir() / ("repos/%d" % repo_id)
    payload_path = Path(payload) if source in ("zip", "sample") else None
    ok = False
    try:
        repo = conn.execute(
            "SELECT id, source FROM repos WHERE id = ?", (repo_id,)
        ).fetchone()
        if repo is None:
            return
        _progress(conn, job_id, repo_id, 0.02, "Starting")
        if source == "url":
            _progress(conn, job_id, repo_id, 0.05, "Cloning %s" % payload)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(dest, ignore_errors=True)
            run_git(
                dest.parent,
                ["clone", "--quiet", "--", payload, str(dest)],
                timeout=MAX_CLONE_SECONDS,
            )
            root = dest
            conn.execute(
                "UPDATE repos SET path = ? WHERE id = ?",
                ("repos/%d" % repo_id, repo_id),
            )
            conn.commit()
            _progress(conn, job_id, repo_id, 0.25, "Clone complete - reading history")
        else:
            _progress(conn, job_id, repo_id, 0.05, "Unzipping archive")
            shutil.rmtree(dest, ignore_errors=True)
            root = extract_zip(payload_path, dest)
            conn.execute(
                "UPDATE repos SET path = ? WHERE id = ?",
                (str(root.relative_to(db.data_dir())), repo_id),
            )
            conn.commit()
            _progress(conn, job_id, repo_id, 0.25, "Archive extracted - reading history")

        try:
            ref_hash = head_hash(root)
        except IngestError:
            raise IngestError("Not a valid git repository - .git could not be read.")
        conn.execute(
            "UPDATE repos SET source = ? WHERE id = ?", (source, repo_id)
        )

        def on_progress(frac, message):
            _progress(conn, job_id, repo_id, 0.25 + 0.70 * frac, message)

        stats = read_history(root, conn, repo_id, on_progress)
        _progress(conn, job_id, repo_id, 0.97, "Finalising")
        conn.execute(
            "UPDATE repos SET status = 'ready', error = NULL, ref_hash = ? WHERE id = ?",
            (ref_hash, repo_id),
        )
        conn.execute(
            "UPDATE jobs SET status = 'done', progress = 1.0, message = ? WHERE id = ?",
            ("Ready - %d commits, %d file rows" % (stats["commits"], stats["rows"]), job_id),
        )
        conn.commit()
        ok = True
        print(
            "  Ingested repo %d: %d commits, %d rows (%d renames, %d binaries skipped)"
            % (repo_id, stats["commits"], stats["rows"], stats["renames"], stats["binaries"])
        )
    except IngestError as exc:
        _fail(conn, job_id, repo_id, str(exc))
    except Exception as exc:  # never let a job thread die silently
        traceback.print_exc()
        _fail(conn, job_id, repo_id, "Unexpected error: %s" % exc)
    finally:
        conn.close()
        if not ok:
            shutil.rmtree(dest, ignore_errors=True)
        if payload_path is not None and _is_inside(payload_path, db.data_dir()):
            try:
                payload_path.unlink()
            except OSError:
                pass


def recover_stale_jobs():
    """Startup hygiene: no threads survive a restart, so nothing is running."""
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE jobs SET status = 'error', message = 'Interrupted by server restart' "
            "WHERE status = 'running'"
        )
        conn.execute(
            "UPDATE repos SET status = 'error', error = 'Interrupted by server restart' "
            "WHERE status IN ('pending', 'running')"
        )
        conn.commit()
    finally:
        conn.close()
