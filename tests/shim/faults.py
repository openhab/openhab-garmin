"""Fault-script grammar for the E2E shim.

A *script* is a JSON document that the runner posts to the shim before a
scenario runs::

    {
      "version": 1,
      "rules": [
        {
          "name": "503-then-recover",
          "match": {"method": "GET", "path_prefix": "/rest/sitemaps/"},
          "times": 2,
          "response": {"status": 503, "body": "service unavailable"}
        }
      ]
    }

Rules are evaluated in order. The first rule whose ``match`` block matches and
which still has uses left wins; the shim then answers with that rule's
``response`` (or drops the connection when ``reset`` is true) instead of
forwarding the request upstream. When no rule matches, the request is proxied
to openHAB unchanged.

Scenarios author the same rules in YAML; the runner normalises them to this
JSON shape. This module is imported by both the runner and the shim server, so
"valid script" has exactly one definition.

Validation is deliberately strict -- unknown keys are rejected rather than
ignored, because a typo in a fault name would otherwise silently turn a fault
scenario into a happy-path scenario that still passes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SCRIPT_VERSION = 1

#: Upper bound for scripted delays. Anything above this is a test bug, not a
#: timeout under test, and would silently burn the scenario budget.
MAX_DELAY_MS = 60_000

#: Upper bound for generated oversized bodies (32 MiB). The app's own response
#: size limit is far below this; the point is to exceed it, not to exhaust the
#: runner's memory.
MAX_BODY_SIZE = 32 * 1024 * 1024

_RULE_KEYS = frozenset({"name", "match", "times", "delay_ms", "reset", "response"})
_MATCH_KEYS = frozenset({"method", "path", "path_prefix", "body_contains"})
_RESPONSE_KEYS = frozenset({"status", "body", "body_size", "content_type"})
_SCRIPT_KEYS = frozenset({"version", "rules"})


class ShimScriptError(ValueError):
    """Raised when a script or a single rule does not follow the grammar."""


@dataclass(frozen=True)
class MatchRule:
    """Which requests a :class:`FaultRule` applies to.

    An empty ``MatchRule`` matches every request. All given conditions must
    hold (logical AND).
    """

    method: Optional[str] = None
    path: Optional[str] = None
    path_prefix: Optional[str] = None
    body_contains: Optional[str] = None

    def matches(self, method: str, path: str, body: bytes) -> bool:
        if self.method is not None and method.upper() != self.method:
            return False
        if self.path is not None and path != self.path:
            return False
        if self.path_prefix is not None and not path.startswith(self.path_prefix):
            return False
        if self.body_contains is not None:
            try:
                text = body.decode("utf-8", errors="replace")
            except Exception:  # pragma: no cover - decode cannot fail here
                return False
            if self.body_contains not in text:
                return False
        return True


@dataclass(frozen=True)
class FaultResponse:
    """The response the shim synthesises instead of proxying upstream."""

    status: int = 200
    body: Optional[str] = None
    content_type: str = "text/plain; charset=utf-8"
    body_size: Optional[int] = None

    def render(self) -> Tuple[int, bytes, Dict[str, str]]:
        """Return ``(status, body_bytes, headers)`` for this response."""
        if self.body_size is not None:
            payload = _sized_json(self.body_size)
            content_type = self.content_type
            if content_type == "text/plain; charset=utf-8":
                content_type = "application/json"
        else:
            payload = (self.body or "").encode("utf-8")
            content_type = self.content_type
        return self.status, payload, {"Content-Type": content_type}


@dataclass(frozen=True)
class FaultRule:
    """One scripted fault."""

    name: str
    match: MatchRule = field(default_factory=MatchRule)
    times: int = 0
    delay_ms: int = 0
    reset: bool = False
    response: Optional[FaultResponse] = None

    @property
    def unlimited(self) -> bool:
        return self.times == 0

    def to_document(self) -> Dict[str, Any]:
        """Return the JSON form of this rule (used for reports)."""
        document: Dict[str, Any] = {"name": self.name}
        match: Dict[str, Any] = {}
        if self.match.method is not None:
            match["method"] = self.match.method
        if self.match.path is not None:
            match["path"] = self.match.path
        if self.match.path_prefix is not None:
            match["path_prefix"] = self.match.path_prefix
        if self.match.body_contains is not None:
            match["body_contains"] = self.match.body_contains
        document["match"] = match
        if self.times:
            document["times"] = self.times
        if self.delay_ms:
            document["delay_ms"] = self.delay_ms
        if self.reset:
            document["reset"] = True
        if self.response is not None:
            response: Dict[str, Any] = {"status": self.response.status}
            if self.response.body_size is not None:
                response["body_size"] = self.response.body_size
            elif self.response.body is not None:
                response["body"] = self.response.body
            response["content_type"] = self.response.content_type
            document["response"] = response
        return document


def _sized_json(size: int) -> bytes:
    """Return a valid JSON document of exactly ``size`` bytes (minimum 2)."""
    head = b'{"id":"shim","padding":"'
    tail = b'"}'
    inner = size - len(head) - len(tail)
    if inner < 0:
        # Too small for the object wrapper: emit a JSON string of exact size.
        return b'"' + b"x" * max(0, size - 2) + b'"'
    return head + b"x" * inner + tail


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ShimScriptError(f"{where} must be a mapping, got {type(value).__name__}")
    return value


def _require_int(value: Any, where: str, minimum: int, maximum: Optional[int] = None) -> int:
    # bool is an int subclass; a `times: true` typo should not pass validation.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ShimScriptError(f"{where} must be an integer, got {type(value).__name__}")
    if value < minimum:
        raise ShimScriptError(f"{where} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ShimScriptError(f"{where} must be <= {maximum}, got {value}")
    return value


def _require_str(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise ShimScriptError(f"{where} must be a string, got {type(value).__name__}")
    return value


def _reject_unknown(mapping: Mapping[str, Any], allowed: frozenset, where: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ShimScriptError(f"{where} has unknown key(s): {', '.join(unknown)}")


def parse_match(value: Any, where: str) -> MatchRule:
    mapping = _require_mapping(value, where)
    _reject_unknown(mapping, _MATCH_KEYS, where)

    method = mapping.get("method")
    if method is not None:
        method = _require_str(method, f"{where}.method").upper()

    path = mapping.get("path")
    if path is not None:
        path = _require_str(path, f"{where}.path")

    path_prefix = mapping.get("path_prefix")
    if path_prefix is not None:
        path_prefix = _require_str(path_prefix, f"{where}.path_prefix")

    if path is not None and path_prefix is not None:
        raise ShimScriptError(f"{where} must not set both 'path' and 'path_prefix'")

    body_contains = mapping.get("body_contains")
    if body_contains is not None:
        body_contains = _require_str(body_contains, f"{where}.body_contains")

    return MatchRule(
        method=method,
        path=path,
        path_prefix=path_prefix,
        body_contains=body_contains,
    )


def parse_response(value: Any, where: str) -> FaultResponse:
    mapping = _require_mapping(value, where)
    _reject_unknown(mapping, _RESPONSE_KEYS, where)

    status = mapping.get("status", 200)
    status = _require_int(status, f"{where}.status", 100, 599)

    body = mapping.get("body")
    if body is not None:
        body = _require_str(body, f"{where}.body")

    body_size = mapping.get("body_size")
    if body_size is not None:
        # 2 is the smallest valid JSON document (`""`).
        body_size = _require_int(body_size, f"{where}.body_size", 2, MAX_BODY_SIZE)

    if body is not None and body_size is not None:
        raise ShimScriptError(f"{where} must not set both 'body' and 'body_size'")

    content_type = mapping.get("content_type", "text/plain; charset=utf-8")
    content_type = _require_str(content_type, f"{where}.content_type")
    if not content_type.strip():
        raise ShimScriptError(f"{where}.content_type must not be empty")

    return FaultResponse(
        status=status,
        body=body,
        content_type=content_type,
        body_size=body_size,
    )


def parse_rule(value: Any, index: int = 0) -> FaultRule:
    """Parse and validate a single fault rule."""
    where = f"rules[{index}]"
    mapping = _require_mapping(value, where)
    _reject_unknown(mapping, _RULE_KEYS, where)

    name = mapping.get("name", f"rule-{index}")
    name = _require_str(name, f"{where}.name")
    if not name.strip():
        raise ShimScriptError(f"{where}.name must not be empty")

    match = parse_match(mapping.get("match", {}), f"{where}.match")

    times = mapping.get("times", 0)
    times = _require_int(times, f"{where}.times", 0)

    delay_ms = mapping.get("delay_ms", 0)
    delay_ms = _require_int(delay_ms, f"{where}.delay_ms", 0, MAX_DELAY_MS)

    reset = mapping.get("reset", False)
    if not isinstance(reset, bool):
        raise ShimScriptError(
            f"{where}.reset must be a boolean, got {type(reset).__name__}"
        )

    raw_response = mapping.get("response")
    if reset:
        if raw_response is not None:
            raise ShimScriptError(
                f"{where} must not set 'response' when 'reset' is true"
            )
        response = None
    else:
        if raw_response is None:
            raise ShimScriptError(
                f"{where} must set either 'response' or 'reset': true"
            )
        response = parse_response(raw_response, f"{where}.response")

    return FaultRule(
        name=name,
        match=match,
        times=times,
        delay_ms=delay_ms,
        reset=reset,
        response=response,
    )


def parse_script(value: Any) -> List[FaultRule]:
    """Parse and validate a complete shim script document."""
    mapping = _require_mapping(value, "script")
    _reject_unknown(mapping, _SCRIPT_KEYS, "script")

    version = mapping.get("version", SCRIPT_VERSION)
    version = _require_int(version, "script.version", 1)
    if version != SCRIPT_VERSION:
        raise ShimScriptError(
            f"script.version must be {SCRIPT_VERSION}, got {version}"
        )

    raw_rules = mapping.get("rules", [])
    if isinstance(raw_rules, (str, bytes)) or not isinstance(raw_rules, Sequence):
        raise ShimScriptError("script.rules must be a list")
    return [parse_rule(rule, index) for index, rule in enumerate(raw_rules)]


class FaultPlan:
    """Ordered fault rules with per-rule use counters.

    Not thread-safe on its own; the shim server guards a single instance with
    its request lock.
    """

    def __init__(self, rules: Optional[Sequence[FaultRule]] = None) -> None:
        self._rules: List[FaultRule] = list(rules or [])
        self._remaining: List[int] = [rule.times for rule in self._rules]

    @classmethod
    def from_script(cls, document: Any) -> "FaultPlan":
        return cls(parse_script(document))

    @property
    def rules(self) -> Tuple[FaultRule, ...]:
        return tuple(self._rules)

    def remaining(self) -> Tuple[int, ...]:
        """Remaining uses per rule; ``0`` means unlimited."""
        return tuple(self._remaining)

    def match(
        self, method: str, path: str, body: bytes = b""
    ) -> Optional[FaultRule]:
        """Return the fault that applies to this request, if any."""
        method = method.upper()
        for index, rule in enumerate(self._rules):
            if rule.times and self._remaining[index] == 0:
                continue
            if not rule.match.matches(method, path, body):
                continue
            if rule.times:
                self._remaining[index] -= 1
            return rule
        return None
