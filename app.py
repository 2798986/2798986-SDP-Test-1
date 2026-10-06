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
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
STATIC_ROOT = (ROOT / "static").resolve()

import db  # noqa: E402  (local module, anchored to ROOT)

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
        if method in ("GET", "HEAD") and path == "/api/health":
            self._send_json({"ok": True, "name": "RAT", "version": "1.0"})
            return
        raise ApiError(404, "Unknown endpoint: %s" % path)

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
