"""Parser for the app's debug-only E2E trace lines.

The app emits one line per event (debug builds only)::

    E2E:<EVENT>;field=<value>;field=<value>

Known events at the time of writing::

    E2E:VIEW;category=<...>;view=<...>
    E2E:CONNECTION;mode=<...>
    E2E:SITEMAP;result=<...>;code=<...>;bytes=<...>
    E2E:COMMAND;result=<...>;code=<...>
    E2E:ERROR;kind=<temporary|critical|fatal>;id=<...>;code=<...>
    E2E:TOAST;id=<...>
    E2E:ERRORVIEW;id=<...>;code=<...>
    E2E:LANGUAGE;code=<...>

Rules this parser follows, so the harness does not break when the app adds
fields or events:

- split the payload on ``;`` and each part on the **first** ``=`` only, so a
  value may itself contain ``=``;
- unknown events and unknown fields are kept but never required, so a scenario
  that asserts on ``ERROR.kind`` keeps working when ``ERROR`` gains a field;
- lines that are not trace lines, or that are malformed, are ignored rather
  than raising: the console stream also carries ordinary ``System.println``
  output;
- events carry no elapsed time, so the receive timestamp is recorded here, when
  the line is first observed.

Scenarios match on structured fields, never on rendered toast text: the text is
localised and is not a stable assertion surface.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence

TRACE_PREFIX = "E2E:"

#: Event names are identifiers: ``ERROR``, ``SITEMAP``, ``ERRORVIEW``.
_EVENT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

#: Events the harness knows about. Unknown events are still parsed and exposed;
#: this tuple exists for validation and documentation, not for filtering.
KNOWN_EVENTS = (
    "VIEW",
    "CONNECTION",
    "SITEMAP",
    "COMMAND",
    "ERROR",
    "TOAST",
    "ERRORVIEW",
    "LANGUAGE",
)


@dataclass(frozen=True)
class TraceEvent:
    """One parsed trace line."""

    raw: str
    event: str
    fields: Mapping[str, str] = field(default_factory=dict)
    received_at: float = 0.0

    @property
    def known(self) -> bool:
        return self.event in KNOWN_EVENTS

    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        return self.fields.get(name, default)

    def to_document(self) -> Dict[str, object]:
        return {
            "event": self.event,
            "fields": dict(self.fields),
            "received_at": round(self.received_at, 3),
            "raw": self.raw,
        }

    def describe(self) -> str:
        parts = ";".join(f"{key}={value}" for key, value in self.fields.items())
        return f"E2E:{self.event}" + (f";{parts}" if parts else "")


def parse_line(line: str, received_at: Optional[float] = None) -> Optional[TraceEvent]:
    """Parse one console line; ``None`` when it is not a trace line."""
    if not isinstance(line, str):
        return None
    text = line.strip()
    index = text.find(TRACE_PREFIX)
    if index < 0:
        return None
    payload = text[index + len(TRACE_PREFIX):].strip()
    if not payload:
        return None

    parts = payload.split(";")
    event = parts[0].strip()
    # Event names are identifiers. Anything else is ordinary console output
    # that happens to contain the prefix.
    if not _EVENT_NAME.match(event):
        return None

    fields: Dict[str, str] = {}
    for part in parts[1:]:
        if not part.strip():
            continue
        key, separator, value = part.partition("=")
        key = key.strip()
        if not separator or not key:
            # A field without '=' is dropped, not fatal: the rest of the line is
            # still usable for assertions.
            continue
        fields[key] = value.strip()

    return TraceEvent(
        raw=text,
        event=event,
        fields=MappingProxyType(fields),
        received_at=time.time() if received_at is None else received_at,
    )


def parse_lines(lines: Iterable[str], received_at: Optional[float] = None) -> List[TraceEvent]:
    """Parse every trace line in a sequence, preserving order."""
    events: List[TraceEvent] = []
    for line in lines:
        event = parse_line(line, received_at=received_at)
        if event is not None:
            events.append(event)
    return events


@dataclass(frozen=True)
class EventMatch:
    """Predicate over trace events used by ``wait_for_event`` and assertions."""

    event: Optional[str] = None
    fields: Mapping[str, str] = field(default_factory=dict)
    contains: Optional[str] = None

    def matches(self, candidate: TraceEvent) -> bool:
        if self.event is not None and candidate.event != self.event:
            return False
        for key, value in self.fields.items():
            if candidate.fields.get(key) != value:
                return False
        if self.contains is not None and self.contains not in candidate.raw:
            return False
        return True

    def by_kind(self, kind: str) -> "EventMatch":
        """Convenience for ``ERROR;kind=...`` assertions."""
        return EventMatch(event="ERROR", fields={"kind": kind}, contains=self.contains)

    def describe(self) -> str:
        details = []
        if self.event:
            details.append(f"event={self.event}")
        for key, value in self.fields.items():
            details.append(f"{key}={value}")
        if self.contains:
            details.append(f"contains={self.contains!r}")
        return " ".join(details) or "<any trace event>"


class TraceLog:
    """Incrementally parse a growing console stream.

    ``feed`` accepts the full line list from the driver on each poll (drivers
    keep their own buffer) and only parses lines it has not seen yet, so events
    keep the timestamp of the first observation.
    """

    def __init__(self, source: Callable[[], Sequence[str]]) -> None:
        self._source = source
        self._seen = 0
        self._events: List[TraceEvent] = []

    @property
    def events(self) -> List[TraceEvent]:
        return list(self._events)

    def poll(self, received_at: Optional[float] = None) -> List[TraceEvent]:
        lines = list(self._source())
        if len(lines) < self._seen:
            # The driver truncated its buffer; restart from what is available.
            self._seen = 0
        new_lines = lines[self._seen:]
        self._seen = len(lines)
        parsed = parse_lines(new_lines, received_at=received_at)
        self._events.extend(parsed)
        return parsed

    def find(self, match: EventMatch) -> Optional[TraceEvent]:
        for event in self._events:
            if match.matches(event):
                return event
        return None

    def wait_for(
        self,
        match: EventMatch,
        timeout_s: float,
        poll_s: float = 0.25,
    ) -> Optional[TraceEvent]:
        """Poll until a matching event arrives; ``None`` on timeout."""
        deadline = time.monotonic() + timeout_s
        while True:
            found = self.find(match)
            if found is not None:
                return found
            if time.monotonic() >= deadline:
                return None
            time.sleep(poll_s)
            self.poll()

    def has_error_view(self) -> bool:
        return any(event.event == "ERRORVIEW" for event in self._events)

    def describe(self, limit: int = 12) -> str:
        if not self._events:
            return "<no trace events>"
        return "; ".join(event.describe() for event in self._events[-limit:])
