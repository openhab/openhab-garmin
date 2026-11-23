"""Shim fault grammar and fault selection tests.

The grammar is shared by the runner (scenario loading) and the shim server
(script installation), so a bug here either disables a fault silently or makes
a scenario unloadable. Both directions are covered: rejection of malformed
scripts, and the exact selection semantics of a valid one.
"""

from __future__ import annotations

import json

import pytest

from shim.faults import (
    MAX_BODY_SIZE,
    MAX_DELAY_MS,
    FaultPlan,
    ShimScriptError,
    parse_rule,
    parse_script,
)


# ---------------------------------------------------------------------------
# acceptance
# ---------------------------------------------------------------------------


def test_minimal_script_parses() -> None:
    rules = parse_script({"rules": []})
    assert rules == []


def test_status_and_body_rule() -> None:
    rules = parse_script(
        {
            "version": 1,
            "rules": [
                {
                    "name": "unavailable",
                    "match": {"method": "get", "path_prefix": "/rest/sitemaps/"},
                    "times": 2,
                    "response": {"status": 503, "body": "nope"},
                }
            ],
        }
    )
    rule = rules[0]
    assert rule.name == "unavailable"
    assert rule.match.method == "GET"  # normalised
    assert rule.times == 2
    assert rule.response is not None
    assert rule.response.status == 503


def test_defaults_are_documented_behaviour() -> None:
    rule = parse_rule({"response": {}})
    assert rule.times == 0  # unlimited
    assert rule.delay_ms == 0
    assert rule.reset is False
    assert rule.match.method is None
    assert rule.response is not None
    assert rule.response.status == 200


def test_reset_rule_has_no_response() -> None:
    rule = parse_rule({"name": "drop", "reset": True})
    assert rule.reset is True
    assert rule.response is None


def test_oversized_body_rule() -> None:
    rule = parse_rule({"response": {"body_size": 1024}})
    assert rule.response is not None
    assert rule.response.body_size == 1024


def test_delay_rule() -> None:
    rule = parse_rule({"delay_ms": 12000, "response": {"status": 200}})
    assert rule.delay_ms == 12000


def test_rule_document_round_trips() -> None:
    rule = parse_rule(
        {
            "name": "x",
            "match": {"path": "/rest/items/A", "body_contains": "ON"},
            "times": 3,
            "delay_ms": 10,
            "response": {"status": 418, "body": "teapot", "content_type": "text/plain"},
        }
    )
    document = rule.to_document()
    assert document["match"] == {"path": "/rest/items/A", "body_contains": "ON"}
    assert document["response"]["status"] == 418
    assert parse_rule(document) == rule


# ---------------------------------------------------------------------------
# rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "document,expected",
    [
        ({"rules": [], "extra": 1}, "unknown key"),
        ({"version": 2, "rules": []}, "version"),
        ({"rules": "nope"}, "must be a list"),
        ({"rules": [{"response": {}, "nope": 1}]}, "unknown key"),
        ({"rules": [{"response": {}, "match": {"url": "/x"}}]}, "unknown key"),
        ({"rules": [{"response": {"code": 200}}]}, "unknown key"),
        ({"rules": [{"response": {"status": 99}}]}, "status"),
        ({"rules": [{"response": {"status": 600}}]}, "status"),
        ({"rules": [{"response": {"status": "500"}}]}, "status"),
        ({"rules": [{"response": {"body": "a", "body_size": 10}}]}, "both"),
        ({"rules": [{"response": {"body_size": 1}}]}, "body_size"),
        ({"rules": [{"response": {"body_size": MAX_BODY_SIZE + 1}}]}, "body_size"),
        ({"rules": [{"reset": True, "response": {}}]}, "must not set 'response'"),
        ({"rules": [{"name": "x"}]}, "must set either"),
        ({"rules": [{"times": -1, "response": {}}]}, "times"),
        ({"rules": [{"times": True, "response": {}}]}, "times"),
        ({"rules": [{"delay_ms": MAX_DELAY_MS + 1, "response": {}}]}, "delay_ms"),
        ({"rules": [{"delay_ms": 1.5, "response": {}}]}, "delay_ms"),
        ({"rules": [{"reset": "yes"}]}, "reset"),
        ({"rules": [{"name": "", "response": {}}]}, "name"),
        (
            {"rules": [{"match": {"path": "/a", "path_prefix": "/b"}, "response": {}}]},
            "both 'path' and 'path_prefix'",
        ),
        ({"rules": [{"response": {"content_type": ""}}]}, "content_type"),
    ],
)
def test_invalid_scripts_are_rejected(document: dict, expected: str) -> None:
    with pytest.raises(ShimScriptError) as error:
        parse_script(document)
    assert expected in str(error.value), str(error.value)


def test_non_mapping_script_is_rejected() -> None:
    with pytest.raises(ShimScriptError):
        parse_script([{"response": {}}])


def test_rule_index_is_reported() -> None:
    with pytest.raises(ShimScriptError) as error:
        parse_script({"rules": [{"response": {}}, {"response": {"status": 0}}]})
    assert "rules[1]" in str(error.value)


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------


def test_match_conditions_are_anded() -> None:
    rule = parse_rule(
        {"match": {"method": "POST", "path": "/rest/items/A", "body_contains": "ON"},
         "response": {"status": 200}}
    )
    assert rule.match.matches("POST", "/rest/items/A", b"ON")
    assert not rule.match.matches("GET", "/rest/items/A", b"ON")
    assert not rule.match.matches("POST", "/rest/items/B", b"ON")
    assert not rule.match.matches("POST", "/rest/items/A", b"OFF")


def test_prefix_match_ignores_query_string_only_if_rule_does() -> None:
    prefix = parse_rule({"match": {"path_prefix": "/rest/sitemaps"}, "response": {}})
    assert prefix.match.matches("GET", "/rest/sitemaps/garmin_e2e?x=1", b"")
    exact = parse_rule({"match": {"path": "/rest/sitemaps/garmin_e2e"}, "response": {}})
    assert not exact.match.matches("GET", "/rest/sitemaps/garmin_e2e?x=1", b"")


def test_empty_match_matches_everything() -> None:
    rule = parse_rule({"response": {}})
    assert rule.match.matches("DELETE", "/anything", b"")


# ---------------------------------------------------------------------------
# fault plan selection
# ---------------------------------------------------------------------------


def _plan() -> FaultPlan:
    return FaultPlan.from_script(
        {
            "rules": [
                {
                    "name": "first-two",
                    "match": {"method": "GET", "path_prefix": "/rest/sitemaps/"},
                    "times": 2,
                    "response": {"status": 503},
                },
                {
                    "name": "posts",
                    "match": {"method": "POST", "path_prefix": "/rest/items/"},
                    "response": {"status": 500},
                },
            ]
        }
    )


def test_rule_applies_exactly_times_then_falls_through() -> None:
    plan = _plan()
    assert plan.match("GET", "/rest/sitemaps/garmin_e2e").name == "first-two"
    assert plan.match("GET", "/rest/sitemaps/garmin_e2e").name == "first-two"
    assert plan.match("GET", "/rest/sitemaps/garmin_e2e") is None


def test_unlimited_rule_never_runs_out() -> None:
    plan = _plan()
    for _ in range(5):
        assert plan.match("POST", "/rest/items/E2E_Light").name == "posts"
    assert plan.remaining()[1] == 0


def test_non_matching_requests_are_proxied() -> None:
    plan = _plan()
    assert plan.match("GET", "/rest/items/E2E_Light") is None


def test_rule_order_is_respected() -> None:
    plan = FaultPlan.from_script(
        {
            "rules": [
                {"name": "specific", "match": {"path": "/a"}, "response": {"status": 404}},
                {"name": "catch-all", "match": {}, "response": {"status": 500}},
            ]
        }
    )
    assert plan.match("GET", "/a").name == "specific"
    assert plan.match("GET", "/b").name == "catch-all"


def test_reset_rule_is_selected_without_a_response() -> None:
    plan = FaultPlan.from_script({"rules": [{"name": "drop", "reset": True}]})
    rule = plan.match("GET", "/rest/sitemaps/garmin_e2e")
    assert rule is not None and rule.reset


def test_remaining_reports_counters() -> None:
    plan = _plan()
    assert plan.remaining() == (2, 0)
    plan.match("GET", "/rest/sitemaps/garmin_e2e")
    assert plan.remaining() == (1, 0)


# ---------------------------------------------------------------------------
# response rendering
# ---------------------------------------------------------------------------


def test_empty_body_renders_zero_bytes() -> None:
    rule = parse_rule({"response": {"status": 200, "body": ""}})
    assert rule.response is not None
    status, payload, _headers = rule.response.render()
    assert status == 200
    assert payload == b""


def test_sized_body_is_exactly_the_requested_size_and_valid_json() -> None:
    for size in (2, 25, 26, 27, 4096):
        rule = parse_rule({"response": {"body_size": size}})
        assert rule.response is not None
        _status, payload, headers = rule.response.render()
        assert len(payload) == size, f"size {size} produced {len(payload)} bytes"
        if size >= 2:
            json.loads(payload.decode("utf-8"))
        assert headers["Content-Type"].startswith("application/json")


def test_malformed_body_is_kept_verbatim() -> None:
    rule = parse_rule({"response": {"body": '{"homepage": ['}})
    assert rule.response is not None
    _status, payload, _headers = rule.response.render()
    assert payload == b'{"homepage": ['
