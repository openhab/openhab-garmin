"""Shim request assertions.

The shim records the **request** and what it **served** (status, byte count,
applied fault name); it never records the served body. These tests pin that
split, because asserting on the wrong one is how a fault-injection scenario
stayed green while testing nothing: `body_equals` compared a stored sitemap
against the (empty) body of a GET and could never match.
"""

from __future__ import annotations

import pytest

from e2e.assertions import (
    AssertionFailure,
    assert_requests,
    describe_observed,
    filter_requests,
)
from e2e.shim import ShimError, ShimRequest

POLICY = "/rest/sitemaps/garmin_e2e"


def _poll(**overrides) -> ShimRequest:
    values = dict(
        seq=1,
        method="GET",
        path=POLICY,
        status=200,
        response_bytes=27168,
    )
    values.update(overrides)
    return ShimRequest(**values)


def test_body_equals_matches_the_request_body_not_the_served_body() -> None:
    poll = _poll(matched_rule="truncated")

    assert filter_requests([poll], body_equals='{"homepage": [') == []
    assert filter_requests([poll], matched_rule="truncated") == [poll]


def test_faulted_selects_records_answered_by_a_scripted_fault() -> None:
    faulted = _poll(seq=1, status=503, response_bytes=19, matched_rule="unavailable")
    upstream = _poll(seq=2)

    assert filter_requests([faulted, upstream], faulted=True) == [faulted]
    assert filter_requests([faulted, upstream], faulted=False) == [upstream]
    assert filter_requests([upstream], faulted=True) == []


def test_a_dropped_connection_counts_as_faulted() -> None:
    dropped = _poll(seq=1, status=0, response_bytes=0, reset=True, matched_rule="drop")

    assert dropped.faulted
    assert filter_requests([dropped], faulted=True) == [dropped]


def test_observed_summary_names_statuses_and_faults() -> None:
    requests = [
        _poll(seq=1),
        _poll(seq=2, status=503, matched_rule="unavailable"),
    ]

    summary = describe_observed(requests)

    assert "2 request(s)" in summary
    assert "200 x1" in summary
    assert "unavailable x1" in summary
    assert "<none> x1" in summary


def test_failure_names_the_expectation_and_what_the_shim_actually_served() -> None:
    requests = [_poll(seq=1), _poll(seq=2, status=503, matched_rule="other")]

    with pytest.raises(AssertionFailure) as error:
        assert_requests(
            requests,
            {"path_prefix": POLICY, "matched_rule": "truncated", "min_count": 2},
            description="The malformed bodies were served from the shim.",
        )

    message = str(error.value)
    assert "expected:" in message
    assert "matched_rule='truncated'" in message
    assert "observed:" in message
    assert "other x1" in message
    assert "matched 0 request(s)" in message


def test_a_stale_shim_image_fails_loudly_instead_of_reporting_an_empty_body() -> None:
    with pytest.raises(ShimError) as error:
        ShimRequest.from_document(
            {"seq": 1, "method": "GET", "path": POLICY, "body": ""}
        )

    assert "request_body" in str(error.value)
