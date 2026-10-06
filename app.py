#!/usr/bin/env python3
"""RAT (Repo Analysis Tool) - local web server.

Standard library only.  Run:  python3 app.py

Environment overrides (all optional):
  PORT      first port to try (default 8000; falls back to 8080, 8888, 9000)
  HOST      bind address (default 127.0.0.1)
  DATA_DIR  data directory (default <repo>/data)
"""

import errno
import json
import os
import re
import shutil
import sys
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
STATIC_ROOT = (ROOT / "static").resolve()

import db  # noqa: E402  (local modules, anchored to ROOT)
import ingest  # noqa: E402

MAX_UPLOAD_BYTES = 1 << 30  # 1 GiB

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".map": "application/json",
}


class ApiError(Exception):
    """Raised by API handlers; converted into a JSON error response."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class RatHandler(BaseHTTPRequestHandler):
    server_version = "RAT/1.0"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ http
    def do_GET(self):
        self._safe("GET")

    def do_HEAD(self):
        self._safe("HEAD")

    def do_POST(self):
        self._safe("POST")

    def do_DELETE(self):
        self._safe("DELETE")

    def _safe(self, method):
        """Every request is wrapped: unexpected exceptions become JSON 500s."""
        self._suppress_body = method == "HEAD"
        self._responded = False
        try:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path.startswith("/api/"):
                self._api(method, path, parsed.query)
            else:
                self._static(path)
        except ApiError as exc:
            self._send_json({"error": exc.message}, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self._send_json({"error": "Internal server error"}, 500)

    def log_message(self, fmt, *args):
        # Job polling is chatty; keep the console readable.
        if "/api/jobs/" in (getattr(self, "requestline", "") or ""):
            return
        sys.stdout.write("  %s\n" % (fmt % args))
        sys.stdout.flush()

    # ------------------------------------------------------------- responses
    def _send_bytes(self, body, status, content_type, cache="no-store"):
        if self._responded:
            return
        self._responded = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not self._suppress_body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self._send_bytes(body, status, "application/json; charset=utf-8")

    # --------------------------------------------------------------- routing
    def _api(self, method, path, query):
        qs = parse_qs(query, keep_blank_values=True)

        if method in ("GET", "HEAD"):
            if path == "/api/health":
                self._send_json({"ok": True, "name": "RAT", "version": "1.0"})
                return
            if path == "/api/repos":
                self._send_repos()
                return
            m = re.fullmatch(r"/api/jobs/(\d+)", path)
            if m:
                self._send_job(int(m.group(1)))
                return
            m = re.fullmatch(r"/api/repos/(\d+)/job", path)
            if m:
                self._send_latest_job(int(m.group(1)))
                return
        if method == "POST":
            if path == "/api/repos":
                self._create_repo_from_url()
                return
            if path == "/api/repos/upload":
                self._create_repo_from_upload(qs)
                return
            if path == "/api/repos/sample":
                self._create_sample_repo()
                return
        if method == "DELETE":
            m = re.fullmatch(r"/api/repos/(\d+)", path)
            if m:
                self._delete_repo(int(m.group(1)))
                return
        raise ApiError(404, "Unknown endpoint: %s" % path)

    # ------------------------------------------------------------ repo API
    def _send_repos(self):
        conn = db.connect()
        try:
            rows = conn.execute(
                """
                SELECT r.id, r.name, r.source, r.status, r.error, r.ref_hash,
                       r.created_at,
                       (SELECT COUNT(*) FROM commits c WHERE c.repo_id = r.id) AS commits,
                       (SELECT COUNT(DISTINCT f.path) FROM files f WHERE f.repo_id = r.id) AS files,
                       (SELECT COUNT(*) FROM authors a WHERE a.repo_id = r.id) AS authors
                FROM repos r ORDER BY r.id
                """
            ).fetchall()
            self._send_json([dict(r) for r in rows])
        finally:
            conn.close()

    def _send_job(self, job_id):
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT id, repo_id, kind, status, progress, message, created_at "
                "FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ApiError(404, "No such job")
            self._send_json(dict(row))
        finally:
            conn.close()

    def _send_latest_job(self, repo_id):
        """Latest job for a repo - lets the UI resume progress after a reload."""
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT id, repo_id, kind, status, progress, message, created_at "
                "FROM jobs WHERE repo_id = ? ORDER BY id DESC LIMIT 1",
                (repo_id,),
            ).fetchone()
            self._send_json(dict(row) if row else None)
        finally:
            conn.close()

    def _start_ingest(self, name, source, payload):
        """Create the repo + job rows, then run the ingestion in a thread."""
        conn = db.connect()
        try:
            now = db.now()
            cur = conn.execute(
                "INSERT INTO repos(name, source, path, status, created_at) "
                "VALUES (?, ?, '', 'running', ?)",
                ((name or "repository")[:200], source, now),
            )
            repo_id = cur.lastrowid
            conn.execute(
                "UPDATE repos SET path = ? WHERE id = ?",
                ("repos/%d" % repo_id, repo_id),
            )
            cur = conn.execute(
                "INSERT INTO jobs(repo_id, kind, status, progress, message, created_at) "
                "VALUES (?, 'ingest', 'running', 0, 'Queued', ?)",
                (repo_id, now),
            )
            job_id = cur.lastrowid
            conn.commit()
        finally:
            conn.close()
        threading.Thread(
            target=ingest.run_ingest,
            args=(job_id, repo_id, source, payload),
            daemon=True,
            name="ingest-%d" % repo_id,
        ).start()
        return repo_id, job_id

    def _create_repo_from_url(self):
        data = self._read_json()
        url = str(data.get("url", "")).strip()
        name = str(data.get("name", "")).strip()
        try:
            ingest.validate_url(url)
        except ingest.IngestError as exc:
            raise ApiError(400, str(exc))
        repo_id, job_id = self._start_ingest(
            name or ingest.name_from_url(url), "url", url
        )
        self._send_json({"repo_id": repo_id, "job_id": job_id, "existing": False}, 201)

    def _create_repo_from_upload(self, qs):
        name = (qs.get("name", [""])[0] or "").strip()
        if name.lower().endswith(".zip"):
            name = name[:-4]
        body = self._read_body(MAX_UPLOAD_BYTES)
        if len(body) < 4 or body[:2] != b"PK":
            raise ApiError(400, "The uploaded file is not a zip archive.")
        incoming = db.data_dir() / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        tmp = incoming / ("upload-%s.zip" % uuid.uuid4().hex[:12])
        tmp.write_bytes(body)
        repo_id, job_id = self._start_ingest(name or "uploaded-repo", "zip", str(tmp))
        self._send_json({"repo_id": repo_id, "job_id": job_id, "existing": False}, 201)

    def _create_sample_repo(self):
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT id, status FROM repos WHERE source = 'sample' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if row is not None and row["status"] != "error":
            self._send_json({"repo_id": row["id"], "job_id": None, "existing": True})
            return
        fixture = ROOT / "demo" / "fixture.zip"
        if not fixture.is_file():
            raise ApiError(500, "demo/fixture.zip is missing from this repository")
        repo_id, job_id = self._start_ingest("Sample fixture", "sample", str(fixture))
        self._send_json({"repo_id": repo_id, "job_id": job_id, "existing": False}, 201)

    def _delete_repo(self, repo_id):
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT id FROM repos WHERE id = ?", (repo_id,)
            ).fetchone()
            if row is None:
                raise ApiError(404, "No such repository")
            for table in ("files", "commits", "authors", "jobs"):
                conn.execute("DELETE FROM %s WHERE repo_id = ?" % table, (repo_id,))
            conn.execute("DELETE FROM repos WHERE id = ?", (repo_id,))
            conn.commit()
        finally:
            conn.close()
        shutil.rmtree(db.data_dir() / ("repos/%d" % repo_id), ignore_errors=True)
        self._send_json({"ok": True})

    # ---------------------------------------------------------- request IO
    def _read_body(self, max_bytes):
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            raise ApiError(411, "Content-Length header is required")
        try:
            length = int(raw_len)
        except ValueError:
            raise ApiError(400, "Invalid Content-Length header")
        if length < 0 or length > max_bytes:
            self.close_connection = True
            raise ApiError(413, "Payload too large (max %d MB)" % (max_bytes // (1 << 20)))
        return self.rfile.read(length)

    def _read_json(self):
        body = self._read_body(1 << 20)
        if not body:
            return {}
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "Request body is not valid JSON")
        if not isinstance(data, dict):
            raise ApiError(400, "Request body must be a JSON object")
        return data

    def _static(self, path):
        if path in ("/", "/index.html"):
            rel = "index.html"
        elif path.startswith("/static/"):
            rel = path[len("/static/"):]
        else:
            raise ApiError(404, "Not found: %s" % path)
        target = (STATIC_ROOT / rel).resolve()
        if target != STATIC_ROOT and STATIC_ROOT not in target.parents:
            raise ApiError(404, "Not found: %s" % path)
        if not target.is_file():
            raise ApiError(404, "Not found: %s" % path)
        ctype = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        cache = "public, max-age=86400" if rel.startswith("vendor/") else "no-store"
        self._send_bytes(target.read_bytes(), 200, ctype, cache)


# ------------------------------------------------------------------ startup
def _port_candidates():
    raw = os.environ.get("PORT", "").strip()
    first = int(raw) if raw.isdigit() and 0 <= int(raw) <= 65535 else 8000
    out, seen = [], set()
    for port in (first, 8080, 8888, 9000, 0):  # 0 = OS-assigned last resort
        if port not in seen:
            seen.add(port)
            out.append(port)
    return out


def main():
    db.init_db()
    ingest.recover_stale_jobs()
    host = os.environ.get("HOST", "").strip() or "127.0.0.1"

    server = None
    for port in _port_candidates():
        try:
            server = ThreadingHTTPServer((host, port), RatHandler)
            break
        except OSError as exc:
            if exc.errno in (errno.EADDRINUSE, errno.EACCES):
                print("  Port %d unavailable, trying next..." % port)
                continue
            raise
    if server is None:
        print("Could not bind any port on %s" % host)
        sys.exit(1)

    url = "http://%s:%d" % (host, server.server_address[1])
    print("")
    print("  RAT - Repo Analysis Tool")
    print("  Serving   %s" % url)
    print("  Data      %s" % db.data_dir())
    print("  Stop      Ctrl+C")
    print("")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
