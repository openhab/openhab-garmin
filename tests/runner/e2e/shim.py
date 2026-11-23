"""Client for the shim's control API.

The shim is the primary assertion surface: the app's requests and the failures
injected into its responses are deterministic, while screenshots are not. This
client is the only thing the runner needs to talk to it.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

DEFAULT_TIMEOUT = 10.0
CONTROL_PREFIX = "/__shim__"


class ShimError(RuntimeError):
    """Raised when the shim cannot be reached or rejects a request."""


@dataclass(frozen=True)
class ShimRequest:
    """One request recorded by the shim."""

    seq: int
    method: str
    path: str
    body: str = ""
    status: int = 0
    reset: bool = False
    matched_rule: Optional[str] = None
    delay_ms: int = 0
    duration_ms: float = 0.0
    error: Optional[str] = None
    headers: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "ShimRequest":
        return cls(
            seq=int(document.get("seq", 0)),
            method=str(document.get("method", "")),
            path=str(document.get("path", "")),
            body=str(document.get("body", "")),
            status=int(document.get("status", 0)),
            reset=bool(document.get("reset", False)),
            matched_rule=document.get("matched_rule"),
            delay_ms=int(document.get("delay_ms", 0)),
            duration_ms=float(document.get("duration_ms", 0.0)),
            error=document.get("error"),
            headers=dict(document.get("headers", {}) or {}),
        )

    def describe(self) -> str:
        outcome = "reset" if self.reset else f"{self.status}"
        rule = f" [{self.matched_rule}]" if self.matched_rule else ""
        body = f" body={self.body!r}" if self.body else ""
        return f"#{self.seq} {self.method} {self.path} -> {outcome}{rule}{body}"


class ShimClient:
    """Talk to the shim's control API."""

    def __init__(self, base_url: str, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        if not base_url:
            raise ShimError("shim base URL is empty")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # -- plumbing ---------------------------------------------------------
    def _call(
        self,
        method: str,
        path: str,
        *,
        body: Optional[bytes] = None,
        expected: tuple = (200,),
    ) -> Any:
        url = f"{self.base_url}{CONTROL_PREFIX}{path}"
        request = urllib.request.Request(url, data=body, method=method)  # noqa: S310
        request.add_header("Accept", "application/json")
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                status = response.status
                payload = response.read()
        except urllib.error.HTTPError as error:
            status = error.code
            payload = error.read()
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ShimError(f"{method} {url} failed: {error}") from None

        if status not in expected:
            detail = payload[:400].decode("utf-8", errors="replace")
            raise ShimError(f"{method} {url} -> {status}: {detail}")
        if not payload:
            return None
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ShimError(f"{method} {url} returned invalid JSON: {error}") from None

    # -- control API ------------------------------------------------------
    def wait_ready(self, timeout_s: float = 60.0, interval_s: float = 0.5) -> None:
        deadline = time.monotonic() + timeout_s
        last = ""
        while time.monotonic() < deadline:
            try:
                self.health()
                return
            except ShimError as error:
                last = str(error)
            time.sleep(interval_s)
        raise ShimError(f"shim at {self.base_url} was not ready within {timeout_s:.0f}s: {last}")

    def health(self) -> Mapping[str, Any]:
        document = self._call("GET", "/health")
        if not isinstance(document, Mapping):
            raise ShimError("shim /health did not return an object")
        return document

    def install_script(self, document: Mapping[str, Any]) -> int:
        """Install a fault script; returns the number of installed rules."""
        body = json.dumps(document).encode("utf-8")
        result = self._call("POST", "/script", body=body)
        if not isinstance(result, Mapping):
            raise ShimError("shim /script did not answer with an object")
        return int(result.get("installed", 0))

    def script(self) -> Mapping[str, Any]:
        document = self._call("GET", "/script")
        if not isinstance(document, Mapping):
            raise ShimError("shim /script did not return an object")
        return document

    def requests(self) -> List[ShimRequest]:
        document = self._call("GET", "/requests")
        if not isinstance(document, Mapping):
            raise ShimError("shim /requests did not return an object")
        raw = document.get("requests", [])
        if not isinstance(raw, list):
            raise ShimError("shim /requests did not return a list")
        return [ShimRequest.from_document(entry) for entry in raw if isinstance(entry, Mapping)]

    def reset(self) -> None:
        """Clear both the installed script and the request trace."""
        self._call("POST", "/reset", body=b"{}")

    def reset_requests(self) -> None:
        """Clear the request trace, keeping the installed script."""
        self._call("POST", "/requests/reset", body=b"{}")

    def describe(self) -> str:
        health = self.health()
        return (
            f"shim at {self.base_url}: upstream={health.get('upstream')} "
            f"rules={health.get('rules')} requests={health.get('requests')}"
        )
