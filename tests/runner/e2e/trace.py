"""Parser for the app's debug-only E2E trace lines.

The app emits one line per event from ``(:debug)`` builds only
(``source/main/infrastructure/logging/E2ETrace.mc``, documented in
``source/main/infrastructure/README.md``)::

    E2E:<EVENT>;field=<value>[;field=<value>...]

The prefix, event names and field names are stable, and fields are emitted in
the documented order::

    E2E:VIEW;category=<sitemap|settings|error|sync|glance>;view=<name>;depth=<n>
    E2E:CONNECTION;mode=<BLE|WIFI_CHECK_PENDING|WIFI|OFFLINE>
    E2E:SITEMAP;result=<success|unchanged|error>;code=<code|->;bytes=<n|->
    E2E:COMMAND;result=<success|error>;code=<code|->;item=<item>;value=<command>
    E2E:INPUT;button=<select|back|next-page|previous-page>;view=<name>;depth=<n>
    E2E:SELECT;kind=<element type>;result=<pushed|empty-page|page-not-ready|no-action|offline|command|settings>;depth=<n>
    E2E:FOCUS;view=<name>;depth=<n>;index=<n>;kind=<element type>;label=<label>
    E2E:ERROR;kind=<temporary|critical|fatal>;id=<id>;code=<code|->;source=<sitemap|command|ui>
    E2E:TOAST;id=<id>
    E2E:ERRORVIEW;id=<id>;code=<code|->
    E2E:LANGUAGE;code=<eng|deu|...|unknown>

``code`` is an HTTP status, an SDK error code (which may be negative, or
carry a prefix such as ``S:-104`` or ``UI:CONFIG``), or ``-``. ``item`` and
``value`` in ``COMMAND``, and ``kind`` and ``label`` in ``FOCUS``, are
percent-encoded (space, ``|``, ``%``, ``;``, CR, LF), so the line format cannot
be broken by a value.

``FOCUS`` is emitted when the focused menu entry changes: after the highlight
moved, and for the entry that becomes focused when a page is pushed or popped.
A repeat of the same ``(view, depth, index, kind, label)`` is suppressed, so a
polling app's repaints do not flood the console. Scrolling between entries
happens inside ``Menu2`` and never reaches the input delegate, so this is the
only event that names the row a press landed on, and it is what lets a scenario
address an element by label instead of by a hard-coded press count.
``focusTraceKind`` reports the sitemap element type (the same value
``SELECT.kind`` reports) or ``Settings``/``Loading``/``MenuItem`` for the
entries the app adds itself; ``label`` carries the sitemap label and is empty
for those entries, because the trace never carries translated chrome.

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
import urllib.parse
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence

TRACE_PREFIX = "E2E:"

#: Event names are identifiers: ``ERROR``, ``SITEMAP``, ``ERRORVIEW``.
_EVENT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

#: Field schema of every known event, in emission order. Unknown events are
#: still parsed and exposed; this mapping exists for scenario validation and
#: documentation, not for filtering.
EVENT_FIELDS: Mapping[str, tuple] = MappingProxyType(
    {
        "VIEW": ("category", "view", "depth"),
        "CONNECTION": ("mode",),
        "SITEMAP": ("result", "code", "bytes"),
        "COMMAND": ("result", "code", "item", "value"),
        "INPUT": ("button", "view", "depth"),
        "SELECT": ("kind", "result", "depth"),
        "FOCUS": ("view", "depth", "index", "kind", "label"),
        "ERROR": ("kind", "id", "code", "source"),
        "TOAST": ("id",),
        "ERRORVIEW": ("id", "code"),
        "LANGUAGE": ("code",),
    }
)

KNOWN_EVENTS = tuple(EVENT_FIELDS)


def validate_match_fields(event: Optional[str], fields: Mapping[str, str]) -> List[str]:
    """Return field names that a known event never emits.

    Used when a scenario is loaded, so ``fields: {kindd: temporary}`` fails with
    the list of valid names instead of silently never matching. Events the
    harness does not know yet accept any field, which keeps forward
    compatibility.
    """
    if event is None or event not in EVENT_FIELDS:
        return []
    known = EVENT_FIELDS[event]
    return [name for name in fields if name not in known]



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
            actual = candidate.fields.get(key)
            if actual is None:
                return False
            if actual == value:
                continue
            # ``COMMAND`` percent-encodes item names and command values (and the
            # app may percent-encode more fields later), so a scenario can write
            # the human-readable value.
            if urllib.parse.unquote(actual) == value:
                continue
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
        """History: True once an ``ERRORVIEW`` event has been observed.

        The app emits ``ERRORVIEW`` when it shows or updates the full-screen
        error view and emits nothing when the error is dismissed, so this stays
        true for the rest of the run after a recovery. It answers "did an error
        view appear", never "is one shown now"; use
        :meth:`is_showing_error_view` for the current state.
        """
        return any(event.event == "ERRORVIEW" for event in self._events)

    def current_view_event(self) -> Optional[TraceEvent]:
        """The newest ``VIEW`` event, or ``None`` before the first one.

        ``ViewStack`` emits one ``VIEW`` line for every stack change (push,
        pop, switch, reset), including the change that dismisses the error
        view, so the newest line describes the view that is on screen now.
        """
        for event in reversed(self._events):
            if event.event == "VIEW":
                return event
        return None

    def current_view_category(self) -> Optional[str]:
        """Category of the current view, e.g. ``sitemap`` or ``error``.

        ``None`` when no ``VIEW`` event with a ``category`` field has been
        observed yet, so callers can distinguish "not known yet" from a real
        category.
        """
        event = self.current_view_event()
        if event is None:
            return None
        return event.get("category")

    def is_showing_error_view(self) -> bool:
        """State: True while the full-screen error view is the current view.

        Derived from the newest ``VIEW`` event instead of
        :meth:`has_error_view`, so an error view that was shown and then
        dismissed stops counting. Before the first ``VIEW`` line, the
        historical ``ERRORVIEW`` is the only evidence available and is used
        instead, so an error raised before the app reported a view is not
        ignored.
        """
        category = self.current_view_category()
        if category is None:
            return self.has_error_view()
        return category == "error"

    def describe(self, limit: int = 12) -> str:
        if not self._events:
            return "<no trace events>"
        return "; ".join(event.describe() for event in self._events[-limit:])

    def counts(self) -> Dict[str, int]:
        """How many events of each name were seen, most frequent first."""
        counts: Dict[str, int] = {}
        for event in self._events:
            counts[event.event] = counts.get(event.event, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

    def of_event(self, name: str) -> List[TraceEvent]:
        """Every event with this name, oldest first."""
        return [event for event in self._events if event.event == name]

    def current_focus_event(self) -> Optional[TraceEvent]:
        """The newest ``FOCUS`` event, or ``None`` before the first one.

        ``FOCUS`` is emitted only when the focused entry changes, so the newest
        line describes where the highlight is now. Because a navigation step
        must never press on a row it has not observed, callers that expect a
        particular menu compare this event's ``view`` and treat a different one
        as "the focus here is not known yet" rather than as a match.
        """
        for event in reversed(self._events):
            if event.event == "FOCUS":
                return event
        return None

    def focus_history(self, limit: int = 12) -> List[str]:
        """The rows the highlight passed through, oldest first.

        Each entry is a ``FOCUS`` line, so a failed navigation names the
        elements that were actually focused instead of only the one that was
        expected. The last ``limit`` lines are returned.
        """
        return [event.describe() for event in self.of_event("FOCUS")[-limit:]]

    def failure_report(self, match: Optional[EventMatch] = None, samples: int = 3) -> str:
        """Why a wait failed, without losing the event that explains it.

        :meth:`describe` shows the last few lines, which for a polling app is a
        wall of ``SITEMAP;result=unchanged``: the single ``ERROR`` or
        ``SITEMAP;result=error`` that explains the failure has scrolled out of
        the window. This keeps the per-event counts, the last events of the
        class the wait wanted, and the values the near-miss fields actually
        carried, so "the app never emitted it" and "it emitted a different
        kind" produce different messages.
        """
        if not self._events:
            return "  trace: the app emitted no trace events at all"
        lines = [
            "  trace: "
            + ", ".join(f"{name} x{count}" for name, count in self.counts().items())
        ]
        awaited = match.event if match is not None else None
        if awaited is not None:
            events = self.of_event(awaited)
            if not events:
                lines.append(f"  no {awaited} event was ever emitted")
            else:
                lines.append(f"  last {awaited} event(s):")
                lines.extend(f"    {event.describe()}" for event in events[-samples:])
                if match is not None and match.fields:
                    for name, expected in match.fields.items():
                        seen: List[str] = []
                        for event in events:
                            value = event.get(name)
                            if value is not None and value not in seen:
                                seen.append(value)
                        if expected not in seen:
                            lines.append(
                                f"  {awaited}.{name} seen: "
                                + (", ".join(seen) if seen else "<never set>")
                                + f" (expected {expected!r})"
                            )
        lines.append("  last events:")
        lines.extend(f"    {event.describe()}" for event in self._events[-samples:])
        return "\n".join(lines)
