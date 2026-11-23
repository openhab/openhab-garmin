"""Trace-line parser tests.

The format is shared with the app's debug trace module (#364):
``E2E:<EVENT>;field=<value>;field=<value>``. The parser must be strict about
the shape and tolerant about unknown events and fields, so a future app change
cannot break every scenario.
"""

from __future__ import annotations

import pytest

from e2e.trace import (
    KNOWN_EVENTS,
    TRACE_PREFIX,
    EventMatch,
    TraceEvent,
    TraceLog,
    parse_line,
    parse_lines,
)


def test_parses_a_known_event() -> None:
    event = parse_line("E2E:ERROR;kind=critical;id=SITEMAP_PARSE;code=0")
    assert event is not None
    assert event.event == "ERROR"
    assert event.fields == {"kind": "critical", "id": "SITEMAP_PARSE", "code": "0"}
    assert event.known


def test_all_documented_events_parse() -> None:
    samples = [
        "E2E:VIEW;category=home;view=menu",
        "E2E:CONNECTION;mode=ble",
        "E2E:SITEMAP;result=ok;code=200;bytes=1234",
        "E2E:COMMAND;result=ok;code=200",
        "E2E:ERROR;kind=temporary;id=EMRES;code=-400",
        "E2E:TOAST;id=EMRES",
        "E2E:ERRORVIEW;id=SITEMAP_PARSE;code=0",
        "E2E:LANGUAGE;code=eng",
    ]
    for sample in samples:
        event = parse_line(sample)
        assert event is not None, sample
        assert event.event in KNOWN_EVENTS, sample


def test_event_without_fields_is_accepted() -> None:
    event = parse_line("E2E:CONNECTION")
    assert event is not None
    assert event.event == "CONNECTION"
    assert event.fields == {}


def test_unknown_event_is_parsed_but_flagged() -> None:
    event = parse_line("E2E:FUTURE;thing=1")
    assert event is not None
    assert event.event == "FUTURE"
    assert not event.known
    assert event.fields == {"thing": "1"}


def test_unknown_field_is_kept_and_does_not_break_matching() -> None:
    event = parse_line("E2E:ERROR;kind=critical;brand_new=42")
    assert event is not None
    assert event.fields["brand_new"] == "42"
    assert EventMatch(event="ERROR", fields={"kind": "critical"}).matches(event)


def test_value_may_contain_equals_and_semicolons_are_delimiters() -> None:
    event = parse_line("E2E:VIEW;label=a=b;category=x")
    assert event is not None
    assert event.fields["label"] == "a=b"
    assert event.fields["category"] == "x"


def test_prefix_may_be_preceded_by_other_output() -> None:
    event = parse_line("SYSTEM: booting\nE2E:CONNECTION;mode=wifi")
    assert event is not None
    assert event.event == "CONNECTION"
    assert event.fields["mode"] == "wifi"


@pytest.mark.parametrize(
    "line",
    [
        "",
        "   ",
        "just a log line",
        "E2E:",
        "E2E:;kind=x",
        "E2E: error without payload",
    ],
)
def test_non_trace_lines_are_ignored(line: str) -> None:
    assert parse_line(line) is None


def test_malformed_field_is_dropped_not_fatal() -> None:
    event = parse_line("E2E:ERROR;broken;kind=fatal")
    assert event is not None
    assert event.fields == {"kind": "fatal"}


def test_receive_timestamp_is_recorded() -> None:
    event = parse_line("E2E:CONNECTION;mode=ble")
    assert event is not None
    assert event.received_at > 0
    explicit = parse_line("E2E:CONNECTION;mode=ble", received_at=1234.5)
    assert explicit is not None and explicit.received_at == 1234.5


def test_parse_lines_preserves_order_and_skips_noise() -> None:
    events = parse_lines(
        ["noise", "E2E:VIEW;view=a", "more noise", "E2E:VIEW;view=b"]
    )
    assert [event.fields["view"] for event in events] == ["a", "b"]


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------


def _event(line: str) -> TraceEvent:
    parsed = parse_line(line)
    assert parsed is not None
    return parsed


def test_match_on_event_only() -> None:
    match = EventMatch(event="TOAST")
    assert match.matches(_event("E2E:TOAST;id=EMRES"))
    assert not match.matches(_event("E2E:ERROR;kind=temporary"))


def test_match_requires_every_field() -> None:
    match = EventMatch(event="ERROR", fields={"kind": "critical", "code": "0"})
    assert match.matches(_event("E2E:ERROR;kind=critical;code=0"))
    assert not match.matches(_event("E2E:ERROR;kind=critical;code=1"))


def test_match_can_use_a_raw_substring() -> None:
    match = EventMatch(contains="S:EMRES")
    assert match.matches(_event("E2E:TOAST;id=S:EMRES"))


def test_match_with_no_conditions_matches_any_trace_event() -> None:
    assert EventMatch().matches(_event("E2E:LANGUAGE;code=eng"))


def test_by_kind_helper() -> None:
    match = EventMatch(event="ERROR").by_kind("fatal")
    assert match.matches(_event("E2E:ERROR;kind=fatal"))
    assert not match.matches(_event("E2E:ERROR;kind=temporary"))


# ---------------------------------------------------------------------------
# incremental log
# ---------------------------------------------------------------------------


class _Buffer:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self) -> list[str]:
        return list(self.lines)


def test_trace_log_only_parses_new_lines() -> None:
    buffer = _Buffer()
    log = TraceLog(buffer)
    buffer.lines.append("E2E:VIEW;view=a")
    first = log.poll()
    assert len(first) == 1
    assert log.poll() == []
    buffer.lines.append("E2E:VIEW;view=b")
    second = log.poll()
    assert [event.fields["view"] for event in second] == ["b"]
    assert len(log.events) == 2


def test_trace_log_survives_a_truncated_buffer() -> None:
    buffer = _Buffer()
    log = TraceLog(buffer)
    buffer.lines = ["E2E:VIEW;view=a", "E2E:VIEW;view=b"]
    log.poll()
    buffer.lines = ["E2E:VIEW;view=c"]
    assert [event.fields["view"] for event in log.poll()] == ["c"]


def test_trace_log_wait_for_returns_the_match() -> None:
    buffer = _Buffer()
    log = TraceLog(buffer)
    buffer.lines.append("E2E:ERROR;kind=temporary")
    found = log.wait_for(EventMatch(event="ERROR", fields={"kind": "temporary"}), 0.5)
    assert found is not None
    assert found.fields["kind"] == "temporary"


def test_trace_log_wait_for_times_out() -> None:
    log = TraceLog(_Buffer())
    assert log.wait_for(EventMatch(event="TOAST"), 0.05) is None


def test_error_view_detection() -> None:
    buffer = _Buffer()
    log = TraceLog(buffer)
    buffer.lines.append("E2E:VIEW;view=a")
    log.poll()
    assert not log.has_error_view()
    buffer.lines.append("E2E:ERRORVIEW;id=X;code=0")
    log.poll()
    assert log.has_error_view()


def test_describe_is_bounded() -> None:
    buffer = _Buffer()
    log = TraceLog(buffer)
    buffer.lines = [f"E2E:VIEW;view={index}" for index in range(30)]
    log.poll()
    text = log.describe(limit=3)
    assert "view=29" in text
    assert "view=0" not in text


def test_prefix_constant_matches_the_app_contract() -> None:
    assert TRACE_PREFIX == "E2E:"
