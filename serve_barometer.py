"""Local Barometer web server with a private, moderated report endpoint."""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

from barometer.submissions import (
    DuplicateSubmission, SubmissionError, SubmissionStore, validate_submission,
)


class RateLimiter:
    def __init__(self, maximum: int = 3, window_seconds: int = 900):
        self.maximum = maximum
        self.window_seconds = window_seconds
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= self.maximum:
                return False
            hits.append(now)
            return True


class BarometerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, public_dir: str, submission_db: str):
        self.public_dir = str(Path(public_dir).resolve())
        self.submission_db = str(Path(submission_db).resolve())
        self.rate_limiter = RateLimiter()
        self._last_prune = 0.0
        super().__init__(address, handler)
        self._prune_expired()

    def _prune_expired(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        Path(self.submission_db).parent.mkdir(parents=True, exist_ok=True)
        with SubmissionStore(self.submission_db) as store:
            store.prune(now - 30 * 86400)
        self._last_prune = now

    def service_actions(self) -> None:
        now = time.time()
        if now - self._last_prune >= 300:
            self._prune_expired(now)


class BarometerHandler(SimpleHTTPRequestHandler):
    server: BarometerServer

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=kwargs.pop("directory", None), **kwargs)

    def translate_path(self, path: str) -> str:
        original = self.directory
        self.directory = self.server.public_dir
        try:
            return super().translate_path(path)
        finally:
            self.directory = original

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'",
        )
        super().end_headers()

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _origin_is_same_host(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlsplit(origin)
        return parsed.scheme in {"http", "https"} and parsed.netloc == self.headers.get("Host")

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/reports":
            self._json(404, {"error": "not found"})
            return
        if not self._origin_is_same_host():
            self._json(403, {"error": "origin is not allowed"})
            return
        if not self.server.rate_limiter.allow(self.client_address[0]):
            self._json(429, {"error": "too many reports; please try again later"})
            return
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self._json(415, {"error": "content type must be application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "invalid content length"})
            return
        if length <= 0 or length > 8192:
            self._json(413, {"error": "submission is empty or too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
            report = validate_submission(payload)
            Path(self.server.submission_db).parent.mkdir(parents=True, exist_ok=True)
            with SubmissionStore(self.server.submission_db) as store:
                report_id = store.add(report)
        except json.JSONDecodeError:
            self._json(400, {"error": "request body is not valid JSON"})
            return
        except DuplicateSubmission as exc:
            self._json(409, {"error": str(exc)})
            return
        except SubmissionError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(201, {"status": "pending", "id": report_id})


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--public-dir", default="observation/public")
    parser.add_argument("--submission-db", default="observation/private/user_reports.db")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    public_dir = Path(args.public_dir)
    if not public_dir.is_dir():
        raise SystemExit(f"public directory does not exist: {public_dir}")
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("WARNING: this stdlib server is for supervised evaluation, not public production")
    server = BarometerServer(
        (args.host, args.port), BarometerHandler,
        str(public_dir), args.submission_db,
    )
    print(f"Barometer available at http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
