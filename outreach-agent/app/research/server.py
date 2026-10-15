"""Run with python -m app.research.server. Owns no persistent application state."""
from __future__ import annotations

import hmac
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

from ..config import load_env
from ..extraction.public_page import inspect_site_local
from ..http_api import JsonHandler, RequestError
from ..website_audit import audit_website_local


class ResearchHandler(JsonHandler):
    max_body_bytes = 8192

    def do_GET(self):
        if self.path == "/healthz":
            return self.json(200, {"service": "research", "version": 1, "status": "ok"})
        return self.json(404, {"error": "Route not found."})

    def post(self):
        if self.path not in {"/v1/audit", "/v1/inspect"}:
            raise RequestError(404, "Route not found.")
        token = self.server.service_token
        if token and not hmac.compare_digest(self.headers.get("Authorization", "").encode("utf-8"), ("Bearer " + token).encode("utf-8")):
            raise RequestError(401, "Invalid service credential.")
        data = self.body()
        url = data.get("url")
        if not isinstance(url, str) or not 1 <= len(url) <= 4096:
            raise RequestError(400, "url must be a non-empty string, at most 4096 characters.")
        try:
            parsed = urlsplit(url if "://" in url else "https://" + url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError
            parsed.port
        except ValueError:
            raise RequestError(400, "Only HTTP(S) website URLs without credentials are supported.") from None
        timeout = data.get("timeout", 8)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 15:
            raise RequestError(400, "timeout must be an integer from 1 to 15 seconds.")
        # Reject excess work rather than spawning unbounded outbound research.
        if not self.server.work_slots.acquire(blocking=False):
            raise RequestError(503, "Research capacity is busy; retry later.")
        try:
            result = audit_website_local(url, timeout) if self.path == "/v1/audit" else inspect_site_local(url, timeout)
            return self.json(200, result)
        finally:
            self.server.work_slots.release()


class ResearchServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, *, token: str = "", concurrency: int = 4):
        if address[0] not in {"127.0.0.1", "localhost", "::1"} and not token:
            raise ValueError("RESEARCH_SERVICE_TOKEN is required for a non-loopback bind.")
        self.service_token = token
        self.work_slots = threading.BoundedSemaphore(concurrency)
        super().__init__(address, ResearchHandler)


def main():
    env = load_env()
    address = (env.get("RESEARCH_HOST", "127.0.0.1"), int(env.get("RESEARCH_PORT", "8766")))
    with ResearchServer(address, token=env.get("RESEARCH_SERVICE_TOKEN", "")) as server:
        print(f"Research service: http://{address[0]}:{address[1]}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
