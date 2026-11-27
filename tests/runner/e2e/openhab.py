"""Minimal openHAB REST client for the harness.

Only what the harness needs:

- readiness polling before a scenario starts
- seeding and reading item states (fixtures are file-based; *state* is REST)
- fetching the sitemap exactly as the app requests it, which is the quickest
  way to tell a fixture problem from an app problem
- sending commands, for scenarios that change openHAB state while the app runs

Standard library only, so the runner image needs no HTTP client package and the
behaviour is easy to reason about. Errors are raised as :class:`OpenHabError`
with the URL and the response body included; a scenario that fails because the
fixture is wrong should say so loudly.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

DEFAULT_TIMEOUT = 10.0


class OpenHabError(RuntimeError):
    """Raised when openHAB cannot be reached or returns an unexpected answer."""


@dataclass(frozen=True)
class ItemState:
    """One item as reported by ``GET /rest/items``."""

    name: str
    type: str
    state: str

    @property
    def is_null(self) -> bool:
        return self.state in ("NULL", "UNDEF", "")


class OpenHabClient:
    """Talk to a running openHAB instance."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        if not base_url:
            raise OpenHabError("openHAB base URL is empty")
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self._authorization: Optional[str] = None
        if username:
            token = base64.b64encode(f"{username}:{password or ''}".encode("utf-8"))
            self._authorization = "Basic " + token.decode("ascii")

    # -- plumbing ---------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[bytes] = None,
        content_type: str = "text/plain",
        timeout: Optional[float] = None,
    ) -> Tuple[int, bytes]:
        url = self.base_url + path.lstrip("/")
        request = urllib.request.Request(url, data=body, method=method)  # noqa: S310
        request.add_header("Accept", "application/json")
        if body is not None:
            request.add_header("Content-Type", content_type)
        if self._authorization:
            request.add_header("Authorization", self._authorization)

        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:  # noqa: S310
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise OpenHabError(f"{method} {url} failed: {error}") from None

    def _expect(self, method: str, path: str, expected: Tuple[int, ...], **kwargs: Any) -> bytes:
        status, payload = self._request(method, path, **kwargs)
        if status not in expected:
            detail = payload[:400].decode("utf-8", errors="replace")
            raise OpenHabError(f"{method} {self.base_url}{path.lstrip('/')} -> {status}: {detail}")
        return payload

    # -- readiness --------------------------------------------------------
    def ping(self, timeout: float = 3.0) -> bool:
        """True when ``GET /rest/`` answers with 200."""
        try:
            status, _ = self._request("GET", "rest/", timeout=timeout)
        except OpenHabError:
            return False
        return status == 200

    def server_version(self, timeout: float = 5.0) -> str:
        """The openHAB runtime version from ``GET /rest/``.

        The top-level ``version`` in that document is the REST API version
        (``8`` for openHAB 5.x) and is identical on every profile. The runtime
        version that identifies the pinned image lives at
        ``runtimeInfo.version``; reading the top-level field would compare
        ``8`` with a pin and never match.

        The healthcheck only proves that the port answers. A container whose
        Java runtime died after failing to bind still reports ``Up (healthy)``,
        so the runtime version is what tells the pinned ``stable`` image from
        the pinned ``dev`` one — and what proves this is openHAB at all.
        """
        status, payload = self._request("GET", "rest/", timeout=timeout)
        if status != 200:
            raise OpenHabError(f"GET {self.base_url}rest/ -> {status}")
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OpenHabError(
                f"GET {self.base_url}rest/ did not return JSON ({error}); the "
                "port answers, but not with an openHAB REST API"
            ) from None
        if not isinstance(document, Mapping):
            raise OpenHabError(f"GET {self.base_url}rest/ did not return a JSON object")
        runtime = document.get("runtimeInfo")
        if not isinstance(runtime, Mapping):
            raise OpenHabError(
                f"GET {self.base_url}rest/ returned JSON without a runtimeInfo "
                "object, so the running openHAB image cannot be identified"
            )
        version = runtime.get("version")
        if not isinstance(version, str) or not version.strip():
            raise OpenHabError(
                f"GET {self.base_url}rest/ returned JSON without "
                "runtimeInfo.version, so the running openHAB image cannot be "
                "identified"
            )
        return version.strip()

    def wait_ready(self, timeout_s: float = 180.0, interval_s: float = 2.0) -> None:
        """Block until ``GET /rest/`` succeeds.

        openHAB answers on port 8080 well before the sitemap provider is ready,
        so readiness is only a coarse gate; scenario-level ``wait_for_request``
        steps cover the rest.
        """
        deadline = time.monotonic() + timeout_s
        last_error = ""
        while time.monotonic() < deadline:
            try:
                status, _ = self._request("GET", "rest/", timeout=5.0)
                if status == 200:
                    return
                last_error = f"HTTP {status}"
            except OpenHabError as error:
                last_error = str(error)
            time.sleep(interval_s)
        raise OpenHabError(
            f"openHAB at {self.base_url} was not ready within {timeout_s:.0f}s "
            f"(last attempt: {last_error})"
        )

    # -- items ------------------------------------------------------------
    def items(self) -> List[ItemState]:
        payload = self._expect("GET", "rest/items", (200,))
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OpenHabError(f"GET rest/items returned invalid JSON: {error}") from None
        if not isinstance(document, list):
            raise OpenHabError("GET rest/items did not return a list")
        return [
            ItemState(
                name=str(item.get("name", "")),
                type=str(item.get("type", "")),
                state=str(item.get("state", "")),
            )
            for item in document
            if isinstance(item, Mapping)
        ]

    def item(self, name: str) -> ItemState:
        quoted = urllib.parse.quote(name, safe="")
        status, payload = self._request("GET", f"rest/items/{quoted}")
        if status == 404:
            raise OpenHabError(
                f"item {name!r} does not exist in openHAB. "
                "Add it to tests/fixtures/openhab/conf/items/ or fix the scenario."
            )
        if status != 200:
            raise OpenHabError(f"GET rest/items/{name} -> {status}")
        document = json.loads(payload.decode("utf-8"))
        return ItemState(
            name=str(document.get("name", name)),
            type=str(document.get("type", "")),
            state=str(document.get("state", "")),
        )

    def sitemaps(self) -> List[str]:
        """Sitemap names from ``GET /rest/sitemaps``.

        openHAB lists a sitemap only after its model parsed, so this is the
        cheapest way to tell "the file is there" from "the file was loaded".
        An empty list on an instance that should have fixtures is a failure,
        not a healthy answer.
        """

        payload = self._expect("GET", "rest/sitemaps", (200,))
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OpenHabError(f"GET rest/sitemaps returned invalid JSON: {error}") from None
        if not isinstance(document, list):
            raise OpenHabError("GET rest/sitemaps did not return a list")
        names: List[str] = []
        for entry in document:
            if isinstance(entry, Mapping):
                name = entry.get("name")
                if isinstance(name, str) and name:
                    names.append(name)
        return sorted(names)

    def set_state(self, name: str, state: str) -> None:
        """Set an item state directly (``PUT /rest/items/{name}/state``).

        This is the documented way to seed state without filesystem access.
        """
        quoted = urllib.parse.quote(name, safe="")
        self._expect(
            "PUT",
            f"rest/items/{quoted}/state",
            (200, 202),
            body=str(state).encode("utf-8"),
        )

    def send_command(self, name: str, command: str) -> None:
        """Send a command (``POST /rest/items/{name}``), as the app does."""
        quoted = urllib.parse.quote(name, safe="")
        self._expect(
            "POST",
            f"rest/items/{quoted}",
            (200, 202),
            body=str(command).encode("utf-8"),
        )

    def seed(self, states: Mapping[str, str]) -> List[str]:
        """Apply a scenario's initial item states.

        Every item is looked up first: a missing item is a fixture bug and must
        name the file to fix, rather than failing later as a NULL state.
        """
        missing: List[str] = []
        for name in states:
            try:
                self.item(name)
            except OpenHabError:
                missing.append(name)
        if missing:
            raise OpenHabError(
                "scenario references item(s) that openHAB does not know: "
                + ", ".join(sorted(missing))
                + ". Add them to tests/fixtures/openhab/conf/items/garmin_e2e.items "
                "and reload the openHAB container."
            )
        for name, state in states.items():
            self.set_state(name, state)
        return sorted(states)

    def wait_item_state(self, name: str, expected: str, timeout_s: float = 15.0) -> None:
        deadline = time.monotonic() + timeout_s
        last = ""
        while time.monotonic() < deadline:
            last = self.item(name).state
            if last == expected:
                return
            time.sleep(0.25)
        raise OpenHabError(
            f"item {name!r} is {last!r} after {timeout_s:.0f}s, expected {expected!r}"
        )

    # -- sitemaps ---------------------------------------------------------
    def sitemap_raw(self, name: str) -> bytes:
        quoted = urllib.parse.quote(name, safe="")
        return self._expect("GET", f"rest/sitemaps/{quoted}", (200,))

    def sitemap_json(self, name: str) -> Any:
        payload = self.sitemap_raw(name)
        if not payload:
            raise OpenHabError(
                f"sitemap {name!r} returned an empty body. Check that "
                f"tests/fixtures/openhab/conf/sitemaps/{name}.sitemap exists and "
                "that the openHAB container has reloaded it."
            )
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OpenHabError(f"sitemap {name!r} returned invalid JSON: {error}") from None

    def item_names_in_sitemap(self, name: str) -> List[str]:
        """Collect item names referenced by a sitemap payload.

        Used by ``status`` and by fixture sanity checks: a sitemap that
        references a missing item would otherwise only fail inside the app.
        """
        document = self.sitemap_json(name)
        found: Dict[str, None] = {}

        def walk(node: Any) -> None:
            if isinstance(node, Mapping):
                item = node.get("item")
                if isinstance(item, Mapping) and isinstance(item.get("name"), str):
                    found[item["name"]] = None
                elif isinstance(item, str):
                    found[item] = None
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(document)
        return sorted(found)
