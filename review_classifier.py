"""Private localhost-only review server for classifier shadow proposals."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
from urllib.parse import unquote, urlsplit

from barometer.review_app import (
    build_review_items, render_review_page, review_metadata,
)
from barometer.reviews import (
    ReviewError, ReviewStore, validate_review_decision,
)


LOCAL_HOSTS = frozenset(("127.0.0.1", "localhost", "::1"))
MAX_REVIEW_BODY = 65536


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, source_db: str, review_db: str):
        self.source_db = str(Path(source_db).resolve())
        self.review_db = str(Path(review_db).resolve())
        self.review_token = secrets.token_urlsafe(32)
        Path(self.review_db).parent.mkdir(parents=True, exist_ok=True)
        super().__init__(address, handler)


class ReviewHandler(BaseHTTPRequestHandler):
    server: ReviewServer

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
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

    def log_message(self, format: str, *args) -> None:
        """Do not copy local client addresses into access logs."""
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(
            status,
            json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _origin_is_same_host(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlsplit(origin)
        return (
            parsed.scheme == "http"
            and parsed.netloc == self.headers.get("Host")
        )

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        try:
            if path in {"/", "/index.html"}:
                self._send(
                    200,
                    render_review_page(self.server.review_token),
                    "text/html; charset=utf-8",
                )
                return
            if path == "/api/bootstrap":
                self._json(200, {
                    "items": build_review_items(
                        self.server.source_db, self.server.review_db),
                    "meta": review_metadata(),
                })
                return
        except (OSError, ValueError) as exc:
            self._json(500, {"error": f"review queue unavailable: {exc}"})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        prefix = "/api/reviews/"
        path = urlsplit(self.path).path
        if not path.startswith(prefix) or len(path) == len(prefix):
            self._json(404, {"error": "not found"})
            return
        if not self._origin_is_same_host():
            self._json(403, {"error": "origin is not allowed"})
            return
        submitted_token = self.headers.get("X-Review-Token", "")
        if not secrets.compare_digest(submitted_token, self.server.review_token):
            self._json(403, {"error": "review token is not valid"})
            return
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            self._json(415, {"error": "content type must be application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "invalid content length"})
            return
        if length <= 0 or length > MAX_REVIEW_BODY:
            self._json(413, {"error": "review is empty or too large"})
            return
        unit_id = unquote(path[len(prefix):])
        try:
            payload = json.loads(self.rfile.read(length))
            items = {
                item["review_unit_id"]: item
                for item in build_review_items(
                    self.server.source_db, self.server.review_db)
            }
            current_item = items.get(unit_id)
            if current_item is None:
                self._json(404, {"error": "review target no longer exists"})
                return
            current_fingerprint = current_item["source_fingerprint"]
            if payload.get("source_fingerprint") != current_fingerprint:
                self._json(409, {
                    "error": "source report changed; reload before reviewing",
                })
                return
            decision = validate_review_decision(
                unit_id, current_item["report_id"], current_fingerprint, payload)
            with ReviewStore(self.server.review_db) as store:
                store.put(decision)
                saved = store.get(unit_id)
        except json.JSONDecodeError:
            self._json(400, {"error": "request body is not valid JSON"})
            return
        except (ReviewError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, {"decision": saved})


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--source-db", default="barometer.db")
    parser.add_argument(
        "--review-db", default="observation/private/classifier_reviews.db")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    if args.host not in LOCAL_HOSTS:
        raise SystemExit("classifier review is private and must bind to localhost")
    if not Path(args.source_db).is_file():
        raise SystemExit(f"source database does not exist: {args.source_db}")
    server = ReviewServer(
        (args.host, args.port), ReviewHandler, args.source_db, args.review_db)
    print(f"Classifier review available at http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
