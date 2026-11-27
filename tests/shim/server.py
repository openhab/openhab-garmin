"""HTTP proxy and fault injector used by the E2E harness.

Sits between the Garmin app and openHAB:

    app  ->  shim (this process)  ->  openHAB

Responsibilities:

1. Record every request the app makes: method, target, request body, headers
   of interest, the status and byte count the shim answered with, how long
   openHAB took, and whether a fault replaced the response. The response body
   is not recorded; see ``RequestRecord``.
2. Replace the response with a scripted fault (status code, empty body,
   malformed body, oversized body, delay, connection reset).
3. Expose a small control API so the runner can install a script and read the
   request trace without touching openHAB.

Control endpoints (never faulted, never proxied):

    GET  /__shim__/health             liveness plus counters
    POST /__shim__/script             install a fault script (JSON body)
    GET  /__shim__/script             the currently installed script
    POST /__shim__/reset              clear script and request trace
    GET  /__shim__/requests           recorded requests, oldest first
    POST /__shim__/requests/reset     clear the request trace only

Everything else is proxied to ``OPENHAB_UPSTREAM``.

Standard library only: the image installs no third-party packages, so the
container runs exactly the code that was reviewed.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

from shim.faults import FaultPlan, FaultRule, ShimScriptError, parse_script

CONTROL_PREFIX = "/__shim__"

#: Request bodies larger than this are truncated in the trace. The app only
#: sends short command payloads, so a larger body means something is wrong and
#: an unbounded record would be a liability.
MAX_RECORDED_BODY = 8 * 1024

#: Headers copied from the app to openHAB. Everything else is dropped, which
#: keeps hop-by-hop headers and the app's Host out of the upstream request.
FORWARDED_HEADERS = ("content-type", "authorization", "accept")

#: Headers copied from openHAB back to the app.
RETURNED_HEADERS = ("content-type", "cache-control")


@dataclass
class RequestRecord:
    """One request observed by the shim, after the fault decision.

    Only the **request** is recorded. ``status`` and ``response_bytes``
    describe what the shim answered with -- the scripted fault's status and
    length when ``matched_rule`` is set, the upstream response otherwise. The
    response body itself is never stored, so "did the app receive the scripted
    body?" is answered by ``matched_rule`` (plus ``response_bytes``), never by
    ``request_body``, which is the body the app sent.
    """

    seq: int
    timestamp: float
    method: str
    path: str
    request_body: str
    headers: Dict[str, str]
    matched_rule: Optional[str] = None
    status: int = 0
    response_bytes: int = 0
    delay_ms: int = 0
    duration_ms: float = 0.0
    reset: bool = False
    error: Optional[str] = None

    def to_document(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "timestamp": round(self.timestamp, 3),
            "method": self.method,
            "path": self.path,
            "request_body": self.request_body,
            "headers": dict(self.headers),
            "matched_rule": self.matched_rule,
            "status": self.status,
            "response_bytes": self.response_bytes,
            "delay_ms": self.delay_ms,
            "duration_ms": round(self.duration_ms, 1),
            "reset": self.reset,
            "error": self.error,
        }


@dataclass
class ShimState:
    """Mutable harness state, guarded by ``lock``."""

    upstream: str
    plan: FaultPlan = field(default_factory=FaultPlan)
    records: List[RequestRecord] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    scripted_rules: List[FaultRule] = field(default_factory=list)
    _seq: int = 0

    def reset(self, keep_script: bool = False) -> None:
        with self.lock:
            self.records.clear()
            if not keep_script:
                self.plan = FaultPlan()
                self.scripted_rules = []

    def install(self, document: Any) -> List[FaultRule]:
        # Parse outside the lock: validation failures must not disturb a script
        # that is already installed and serving requests.
        rules = parse_script(document)
        with self.lock:
            self.plan = FaultPlan(rules)
            self.scripted_rules = list(rules)
        return rules

    def next_seq(self) -> int:
        """Reserve the next request sequence number (call with the lock held)."""
        self._seq += 1
        return self._seq

    def append(self, record: RequestRecord) -> None:
        with self.lock:
            self.records.append(record)


class ShimHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "openhab-garmin-shim/1"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        sys.stderr.write("shim: " + (fmt % args) + "\n")

    @property
    def state(self) -> ShimState:
        return self.server.state  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def do_PUT(self) -> None:  # noqa: N802
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle()

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle()

    # -- request handling -------------------------------------------------
    def _handle(self) -> None:
        body = self._read_body()
        self._body = body

        if self.path.startswith(CONTROL_PREFIX):
            self._handle_control(body)
            return

        started = time.monotonic()
        rule: Optional[FaultRule] = None
        with self.state.lock:
            seq = self.state.next_seq()
            rule = self.state.plan.match(self.command, self.path, body)

        record = RequestRecord(
            seq=seq,
            timestamp=time.time(),
            method=self.command,
            path=self.path,
            request_body=body[:MAX_RECORDED_BODY].decode("utf-8", errors="replace"),
            headers=self._recorded_headers(),
            matched_rule=rule.name if rule else None,
            delay_ms=rule.delay_ms if rule else 0,
        )

        if rule is None:
            self._proxy(record)
        elif rule.reset:
            self._reset_connection(record, started)
            return
        else:
            self._render_fault(rule, record, started)

        record.duration_ms = (time.monotonic() - started) * 1000.0
        self.state.append(record)

    def _read_body(self) -> bytes:
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        try:
            size = int(length)
        except ValueError:
            return b""
        if size <= 0:
            return b""
        return self.rfile.read(size)

    def _recorded_headers(self) -> Dict[str, str]:
        recorded: Dict[str, str] = {}
        for name in FORWARDED_HEADERS:
            value = self.headers.get(name)
            if value is not None:
                if name == "authorization":
                    # Never persist credentials; record only that they were used.
                    recorded[name] = "<redacted>"
                else:
                    recorded[name] = value
        return recorded

    def _proxy(self, record: RequestRecord) -> None:
        url = self.state.upstream.rstrip("/") + self.path
        payload = getattr(self, "_body", b"")
        request = urllib.request.Request(  # noqa: S310 - compose-internal URL
            url, data=payload or None, method=self.command
        )
        for name in FORWARDED_HEADERS:
            value = self.headers.get(name)
            if value is not None:
                request.add_header(name, value)

        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                response_body = response.read()
                record.status = response.status
                record.response_bytes = len(response_body)
                self._send(
                    response.status, response_body, self._response_headers(response.headers)
                )
        except urllib.error.HTTPError as error:
            response_body = error.read()
            record.status = error.code
            record.response_bytes = len(response_body)
            self._send(error.code, response_body, self._response_headers(error.headers))
        except (urllib.error.URLError, socket.timeout, ConnectionError) as error:
            # openHAB not reachable (yet). 502 keeps the app's error handling in
            # play instead of hanging the simulator.
            payload = b"shim: upstream unavailable\n"
            record.status = 502
            record.response_bytes = len(payload)
            record.error = f"{type(error).__name__}: {error}"
            self._send(502, payload, {"Content-Type": "text/plain"})

    def _response_headers(self, upstream_headers: Any) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        for name in RETURNED_HEADERS:
            value = upstream_headers.get(name)
            if value is not None:
                headers[name] = value
        return headers

    def _render_fault(self, rule: FaultRule, record: RequestRecord, started: float) -> None:
        assert rule.response is not None  # guaranteed by parse_rule
        if rule.delay_ms:
            time.sleep(rule.delay_ms / 1000.0)
        status, payload, headers = rule.response.render()
        record.status = status
        record.response_bytes = len(payload)
        self._send(status, payload, headers)

    def _reset_connection(self, record: RequestRecord, started: float) -> None:
        record.reset = True
        record.duration_ms = (time.monotonic() - started) * 1000.0
        record.status = 0
        self.state.append(record)
        try:
            # SO_LINGER with a zero timeout makes close() send a RST instead of
            # a FIN, which is what the app sees as a dropped connection.
            self.connection.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
            )
            self.connection.close()
        except OSError as error:  # pragma: no cover - best effort
            self.log_message("reset failed: %s", error)
        finally:
            self.close_connection = True

    def _send(self, status: int, payload: bytes, headers: Dict[str, str]) -> None:
        self.send_response_only(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)
        self.wfile.flush()

    # -- control API ------------------------------------------------------
    def _handle_control(self, body: bytes) -> None:
        path = self.path.split("?", 1)[0]

        if path == f"{CONTROL_PREFIX}/health" and self.command == "GET":
            with self.state.lock:
                self._json(
                    200,
                    {
                        "status": "ok",
                        "upstream": self.state.upstream,
                        "rules": len(self.state.scripted_rules),
                        "requests": len(self.state.records),
                    },
                )
            return

        if path == f"{CONTROL_PREFIX}/script" and self.command == "POST":
            try:
                document = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                self._json(400, {"error": f"invalid JSON: {error}"})
                return
            try:
                rules = self.state.install(document)
            except ShimScriptError as error:
                self._json(400, {"error": str(error)})
                return
            self._json(200, {"installed": len(rules)})
            return

        if path == f"{CONTROL_PREFIX}/script" and self.command == "GET":
            with self.state.lock:
                document = {
                    "version": 1,
                    "rules": [rule.to_document() for rule in self.state.scripted_rules],
                }
            self._json(200, document)
            return

        if path == f"{CONTROL_PREFIX}/reset" and self.command == "POST":
            self.state.reset()
            self._json(200, {"reset": True})
            return

        if path == f"{CONTROL_PREFIX}/requests" and self.command == "GET":
            with self.state.lock:
                records = [record.to_document() for record in self.state.records]
            self._json(200, {"requests": records})
            return

        if path == f"{CONTROL_PREFIX}/requests/reset" and self.command == "POST":
            self.state.reset(keep_script=True)
            self._json(200, {"reset": "requests"})
            return

        self._json(404, {"error": f"unknown control endpoint {path}"})

    def _json(self, status: int, document: Any) -> None:
        payload = json.dumps(document).encode("utf-8")
        self._send(status, payload, {"Content-Type": "application/json"})


class ShimServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: Any, upstream: str) -> None:
        super().__init__(address, ShimHandler)
        self.state = ShimState(upstream=upstream)


def main() -> int:
    upstream = os.environ.get("OPENHAB_UPSTREAM", "http://openhab-stable:8080")
    host = os.environ.get("SHIM_BIND", "0.0.0.0")
    port = int(os.environ.get("SHIM_PORT", "8080"))

    server = ShimServer((host, port), upstream)
    print(f"shim: listening on {host}:{port}, upstream {upstream}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
