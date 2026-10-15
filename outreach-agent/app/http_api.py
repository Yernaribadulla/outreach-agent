"""Bounded JSON transport for local HTTP services, independent of business logic."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit


class RequestError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class JsonHandler(BaseHTTPRequestHandler):
    max_body_bytes = 1_048_576

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def json(self, status: int, payload: dict):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body(self) -> dict:
        if self.headers.get("Transfer-Encoding"):
            raise RequestError(400, "Transfer-Encoding is not supported.")
        lengths = self.headers.get_all("Content-Length", [])
        try:
            if len(lengths) > 1:
                raise ValueError
            size = int(lengths[0]) if lengths else 0
            if size < 0:
                raise ValueError
        except ValueError:
            raise RequestError(400, "Invalid Content-Length.") from None
        if size > self.max_body_bytes:
            raise RequestError(413, "JSON body is too large.")
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if size and media_type != "application/json":
            raise RequestError(415, "Content-Type must be application/json.")
        try:
            raw = self.rfile.read(size)
        except (TimeoutError, OSError):
            raise RequestError(408, "Request body timed out.") from None
        if len(raw) != size:
            raise RequestError(400, "Incomplete JSON body.")
        try:
            data = json.loads(raw or b"{}")
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise RequestError(400, "Invalid JSON body.") from None
        if not isinstance(data, dict):
            raise RequestError(400, "JSON body must be an object.")
        return data

    def do_POST(self):
        try:
            # A public webpage must not mutate a user's local dashboard.
            origin = self.headers.get("Origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                    same_origin = parsed.scheme in {"http", "https"} and parsed.netloc == self.headers.get("Host")
                except ValueError:
                    same_origin = False
                if not same_origin:
                    raise RequestError(403, "Cross-origin requests are not allowed.")
            return self.post()
        except RequestError as exc:
            self.close_connection = True
            return self.json(exc.status, {"error": str(exc)})

    def log_message(self, *_):
        pass
