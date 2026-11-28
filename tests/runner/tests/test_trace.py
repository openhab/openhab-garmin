"""Trace-line parser tests.

The format is shared with the app's debug trace module (#364):
``E2E:<EVENT>;field=<value>;field=<value>``. The parser must be strict about
the shape and tolerant about unknown events and fields, so a future app change
cannot break every scenario.
"""

from __future__ import annotations

import pytest

from e2e.trace import (
    EVENT_FIELDS,
    KNOWN_EVENTS,
    TRACE_PREFIX,
    EventMatch,
    TraceEvent,
    TraceLog,
    parse_line,
    parse_lines,
    validate_match_fields,
)

#: Verbatim sample lines from the ``E2ETrace`` class header and emit methods
#: (source/main/infrastructure/logging/E2ETrace.mc). They are the contract: if
#: the app changes field names or ordering, this list must change with it.
REAL_SAMPLES = (
    "E2E:VIEW;category=sitemap;view=HomepageMenu;depth=2",
    "E2E:VIEW;category=glance;view=GlanceSitemapView;depth=1",
    "E2E:VIEW;category=error;view=ErrorView;depth=3",
    "E2E:VIEW;category=sync;view=SystemSyncView;depth=2",
    "E2E:VIEW;category=sitemap;view=ActionMenu;depth=3",
    "E2E:CONNECTION;mode=BLE",
    "E2E:CONNECTION;mode=WIFI_CHECK_PENDING",
    "E2E:CONNECTION;mode=WIFI",
    "E2E:CONNECTION;mode=OFFLINE",
    "E2E:SITEMAP;result=success;code=200;bytes=4096",
    "E2E:SITEMAP;result=unchanged;code=200;bytes=4096",
    "E2E:SITEMAP;result=error;code=-402;bytes=-",
    "E2E:COMMAND;result=success;code=200;item=E2E_Light;value=ON",
    "E2E:COMMAND;result=error;code=S:-104;item=Kitchen%20Light;value=ON",
    "E2E:INPUT;button=select;view=HomepageMenu;depth=1",
    "E2E:INPUT;button=back;view=PageMenu;depth=2",
    "E2E:SELECT;kind=Frame;result=pushed;depth=1",
    "E2E:SELECT;kind=Switch;result=command;depth=2",
    "E2E:FOCUS;view=HomepageMenu;depth=1;index=1;kind=Frame;label=Player",
    "E2E:FOCUS;view=PageMenu;depth=2;index=0;kind=Switch;label=Kitchen%20Light",
    "E2E:FOCUS;view=HomepageMenu;depth=1;index=4;kind=Settings;label=",
    "E2E:ERROR;kind=temporary;id=communication.sitemap;code=-400;source=sitemap",
    "E2E:ERROR;kind=critical;id=communication.sitemap;code=404;source=sitemap",
    "E2E:ERROR;kind=fatal;id=settings.changed;code=-;source=ui",
    "E2E:TOAST;id=S:EMRES",
    "E2E:ERRORVIEW;id=S:-104;code=-",
    "E2E:LANGUAGE;code=eng",
)


def test_parses_a_known_event() -> None:
    event = parse_line("E2E:ERROR;kind=critical;id=communication.sitemap;code=404;source=sitemap")
    assert event is not None
    assert event.event == "ERROR"
    assert event.fields == {
        "kind": "critical",
        "id": "communication.sitemap",
        "code": "404",
        "source": "sitemap",
    }
    assert event.known


@pytest.mark.parametrize("sample", REAL_SAMPLES)
def test_real_app_samples_parse(sample: str) -> None:
    event = parse_line(sample)
    assert event is not None, sample
    assert event.event in KNOWN_EVENTS, sample
    expected = EVENT_FIELDS[event.event]
    # Every documented field is present, in the documented order.
    assert tuple(event.fields) == expected, sample


def test_real_samples_carry_no_timestamp_so_it_is_stamped_on_receipt() -> None:
    first = parse_line(REAL_SAMPLES[0])
    second = parse_line(REAL_SAMPLES[0])
    assert first is not None and second is not None
    assert "timestamp" not in first.raw and "time=" not in first.raw
    assert first.received_at > 0 and second.received_at >= first.received_at


def test_error_codes_keep_their_prefix_and_negative_sign() -> None:
    event = parse_line("E2E:ERRORVIEW;id=S:-104;code=-")
    assert event is not None
    assert event.fields["id"] == "S:-104"
    assert event.fields["code"] == "-"


def test_command_values_are_percent_encoded_on_the_wire() -> None:
    event = parse_line("E2E:COMMAND;result=success;code=200;item=Kitchen%20Light;value=OPEN%7CON")
    assert event is not None
    assert event.fields["item"] == "Kitchen%20Light"
    assert event.fields["value"] == "OPEN%7CON"


def test_a_focus_label_is_matched_in_its_human_readable_form() -> None:
    # The app percent-encodes label like COMMAND.item, including the ';' that
    # would otherwise split the line, so a scenario writes the label as it
    # appears on screen.
    event = parse_line(
        "E2E:FOCUS;view=PageMenu;depth=2;index=2;kind=Switch;label=Living%3BRoom%20Lamp"
    )
    assert event is not None
    assert event.fields["label"] == "Living%3BRoom%20Lamp"
    match = EventMatch(event="FOCUS", fields={"label": "Living;Room Lamp"})
    assert match.matches(event)


def test_a_focus_position_is_matched_as_the_documented_decimal() -> None:
    event = parse_line(
        "E2E:FOCUS;view=HomepageMenu;depth=1;index=3;kind=Frame;label=Empty"
    )
    assert event is not None
    # Scenario `index: 3` is canonicalised to the string the wire carries.
    assert EventMatch(event="FOCUS", fields={"index": "3"}).matches(event)
    assert not EventMatch(event="FOCUS", fields={"index": "1"}).matches(event)


def test_an_empty_focus_label_is_a_field_not_a_missing_one() -> None:
    # A non-sitemap entry reports no label on purpose; it must still parse as
    # an empty field, so `label: ""` can be asserted deliberately.
    event = parse_line("E2E:FOCUS;view=HomepageMenu;depth=1;index=4;kind=Settings;label=")
    assert event is not None
    assert "label" in event.fields
    assert event.fields["label"] == ""


def test_match_accepts_the_human_readable_command_value() -> None:
    event = parse_line("E2E:COMMAND;result=success;code=200;item=Kitchen%20Light;value=OPEN%7CON")
    assert event is not None
    match = EventMatch(event="COMMAND", fields={"item": "Kitchen Light", "value": "OPEN|ON"})
    assert match.matches(event)


@pytest.mark.parametrize(
    "event,fields,expected",
    [
        ("ERROR", {"kind": "fatal"}, []),
        ("ERROR", {"kindd": "fatal"}, ["kindd"]),
        ("SITEMAP", {"result": "success", "code": "200", "bytes": "1"}, []),
        ("VIEW", {"depth": "2", "view": "HomepageMenu"}, []),
        ("FUTURE_EVENT", {"anything": "goes"}, []),
    ],
)
def test_validate_match_fields(event: str, fields: dict, expected: list) -> None:
    assert validate_match_fields(event, fields) == expected


def test_validate_match_fields_without_event_accepts_anything() -> None:
    assert validate_match_fields(None, {"kind": "fatal"}) == []


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


def test_is_showing_error_view_tracks_the_current_view_not_the_history() -> None:
    buffer = _Buffer()
    log = TraceLog(buffer)
    buffer.lines = [
        "E2E:VIEW;category=sitemap;view=LoadingView;depth=1",
        "E2E:ERROR;kind=critical;id=communication.sitemap;code=404;source=sitemap",
        "E2E:ERRORVIEW;id=S:-104;code=-",
        "E2E:VIEW;category=error;view=ErrorView;depth=1",
    ]
    log.poll()
    assert log.current_view_category() == "error"
    assert log.is_showing_error_view()
    assert log.has_error_view()

    # Recovering from a critical error pops the error view and re-emits the
    # view underneath it.
    buffer.lines.append("E2E:VIEW;category=sitemap;view=HomepageMenu;depth=1")
    log.poll()
    assert log.current_view_category() == "sitemap"
    assert not log.is_showing_error_view()
    assert log.has_error_view(), "the run still contains an error view"


def test_current_view_category_ignores_non_view_events() -> None:
    buffer = _Buffer()
    log = TraceLog(buffer)
    buffer.lines = ["E2E:VIEW;category=settings;view=SettingsMenu;depth=2"]
    log.poll()
    buffer.lines.append("E2E:SITEMAP;result=error;code=-402;bytes=-")
    log.poll()
    assert log.current_view_category() == "settings"


def test_current_view_category_is_unknown_before_the_first_view() -> None:
    log = TraceLog(_Buffer())
    assert log.current_view_category() is None
    assert log.current_view_event() is None
    assert not log.is_showing_error_view()


def test_is_showing_error_view_falls_back_to_history_before_the_first_view() -> None:
    buffer = _Buffer()
    log = TraceLog(buffer)
    buffer.lines = ["E2E:ERRORVIEW;id=S:-104;code=-"]
    log.poll()
    assert log.current_view_category() is None
    assert log.is_showing_error_view(), "an error raised before any view must not be ignored"


def test_error_view_history_survives_the_recovery() -> None:
    buffer = _Buffer()
    log = TraceLog(buffer)
    buffer.lines = [
        "E2E:ERRORVIEW;id=UI:CONFIG;code=-",
        "E2E:VIEW;category=error;view=ErrorView;depth=1",
        "E2E:VIEW;category=sitemap;view=HomepageMenu;depth=1",
    ]
    log.poll()
    assert not log.is_showing_error_view()
    assert log.has_error_view()


def test_current_focus_event_is_the_newest_row_the_highlight_reached() -> None:
    buffer = _Buffer()
    log = TraceLog(buffer)
    buffer.lines = [
        "E2E:FOCUS;view=HomepageMenu;depth=1;index=0;kind=Frame;label=Switches",
        "E2E:FOCUS;view=HomepageMenu;depth=1;index=1;kind=Frame;label=Player",
        "E2E:SITEMAP;result=unchanged;code=200;bytes=4096",
    ]
    log.poll()
    event = log.current_focus_event()
    assert event is not None
    assert event.get("label") == "Player"
    # A later non-FOCUS event must not change where the highlight is reported.
    assert event.get("index") == "1"


def test_current_focus_event_is_unknown_before_the_first_row() -> None:
    log = TraceLog(_Buffer())
    assert log.current_focus_event() is None


def test_current_focus_event_ignores_an_earlier_appearance_of_the_view() -> None:
    """The homepage is shown again: its previous row must not count as current."""
    buffer = _Buffer()
    log = TraceLog(buffer)
    buffer.lines = [
        "E2E:VIEW;category=sitemap;view=HomepageMenu;depth=1",
        "E2E:FOCUS;view=HomepageMenu;depth=1;index=1;kind=Frame;label=Player",
        "E2E:VIEW;category=sitemap;view=PageMenu;depth=2",
        "E2E:FOCUS;view=PageMenu;depth=2;index=0;kind=Switch;label=Audio",
        "E2E:VIEW;category=sitemap;view=HomepageMenu;depth=1",
    ]
    log.poll()
    # The homepage is the newest view and has not reported its row yet.
    assert log.current_focus_event("HomepageMenu") is None
    # The page's row belongs to a view that is no longer on screen.
    assert log.focus_events("PageMenu") == []
    assert log.current_focus_event() is None


def test_focus_events_are_scoped_to_the_newest_view() -> None:
    buffer = _Buffer()
    log = TraceLog(buffer)
    buffer.lines = [
        "E2E:VIEW;category=sitemap;view=HomepageMenu;depth=1",
        "E2E:FOCUS;view=HomepageMenu;depth=1;index=1;kind=Frame;label=Player",
        "E2E:VIEW;category=sitemap;view=PageMenu;depth=2",
        "E2E:FOCUS;view=PageMenu;depth=2;index=0;kind=Switch;label=Audio",
    ]
    log.poll()
    assert [event.get("label") for event in log.focus_events("PageMenu")] == ["Audio"]
    assert log.focus_events("HomepageMenu") == []
    assert log.current_focus_event().get("label") == "Audio"


def test_focus_history_names_every_row_the_highlight_passed_through() -> None:
    buffer = _Buffer()
    log = TraceLog(buffer)
    buffer.lines = [
        "E2E:FOCUS;view=HomepageMenu;depth=1;index=0;kind=Frame;label=Switches",
        "E2E:FOCUS;view=HomepageMenu;depth=1;index=1;kind=Frame;label=Player",
        "E2E:FOCUS;view=PageMenu;depth=2;index=0;kind=Switch;label=Audio",
    ]
    log.poll()
    history = log.focus_history()
    assert history == [
        "E2E:FOCUS;view=HomepageMenu;depth=1;index=0;kind=Frame;label=Switches",
        "E2E:FOCUS;view=HomepageMenu;depth=1;index=1;kind=Frame;label=Player",
        "E2E:FOCUS;view=PageMenu;depth=2;index=0;kind=Switch;label=Audio",
    ]


def test_focus_history_is_bounded() -> None:
    buffer = _Buffer()
    log = TraceLog(buffer)
    buffer.lines = [
        f"E2E:FOCUS;view=HomepageMenu;depth=1;index={index};kind=Frame;label=Row{index}"
        for index in range(30)
    ]
    log.poll()
    history = log.focus_history(limit=3)
    assert len(history) == 3
    assert "Row29" in history[-1]


def test_describe_is_bounded() -> None:
    buffer = _Buffer()
    log = TraceLog(buffer)
    buffer.lines = [f"E2E:VIEW;view={index}" for index in range(30)]
    log.poll()
    text = log.describe(limit=3)
    assert "view=29" in text
    assert "view=0" not in text


def test_failure_report_keeps_an_error_the_poll_tail_would_hide() -> None:
    """The one ERROR event must not scroll out of an 11-line tail of polls."""
    buffer = _Buffer()
    log = TraceLog(buffer)
    buffer.lines = [
        "E2E:VIEW;category=sitemap;view=HomepageMenu;depth=1",
        "E2E:ERROR;kind=critical;id=communication.sitemap;code=503;source=sitemap",
    ] + ["E2E:SITEMAP;result=unchanged;code=200;bytes=27168"] * 11
    log.poll()

    report = log.failure_report(EventMatch(event="ERROR", fields={"kind": "temporary"}))

    assert "ERROR x1" in report
    assert "kind=critical" in report
    assert "ERROR.kind seen: critical" in report
    assert "'temporary'" in report


def test_failure_report_says_when_the_event_was_never_emitted() -> None:
    buffer = _Buffer()
    log = TraceLog(buffer)
    buffer.lines = ["E2E:SITEMAP;result=unchanged;code=200;bytes=4096"]
    log.poll()

    report = log.failure_report(EventMatch(event="ERROR"))

    assert "no ERROR event was ever emitted" in report
    assert "SITEMAP x1" in report


def test_prefix_constant_matches_the_app_contract() -> None:
    assert TRACE_PREFIX == "E2E:"
