"""Profile detection: which pinned openHAB image actually serves the port.

Profiles are exclusive — one openHAB serves one tree — so a full run needs one
pass per profile. These tests pin the detection rule: the version on ``GET
/rest/`` identifies the image, a stack that does not answer or serves an
unpinned version is a failure (not a skip), and a `dev` scenario on a `stable`
stack is skipped with a reason instead of failing in the guard.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

import pytest

from e2e import fixtures
from e2e.openhab import OpenHabClient, OpenHabError


class _Item:
    def __init__(self, name: str) -> None:
        self.name = name


class _OpenHab:
    """A stubbed REST client: no socket, only the three methods detection uses."""

    def __init__(
        self,
        items: Sequence[str] = (),
        sitemaps: Sequence[str] = (),
        *,
        version: str = "5.2.5",
        error: Optional[str] = None,
    ) -> None:
        self.base_url = "http://127.0.0.1:8080/"
        self._items = [_Item(name) for name in items]
        self._sitemaps = list(sitemaps)
        self._version = version
        self._error = error

    def server_version(self) -> str:
        if self._error is not None:
            raise OpenHabError(self._error)
        return self._version

    def items(self) -> List[_Item]:
        return self._items

    def sitemaps(self) -> List[str]:
        return self._sitemaps


def _expected(profile: str):
    return fixtures.load_expected(profile)


def _stack(profile: str, *, version: Optional[str] = None) -> _OpenHab:
    expected = _expected(profile)
    return _OpenHab(
        list(expected.items),
        list(expected.sitemaps),
        version=version or fixtures.pinned_versions()[profile],
    )


def test_the_pinned_tag_reduces_to_the_version_the_rest_api_reports() -> None:
    assert fixtures.served_version("5.2.5-debian") == "5.2.5"
    assert fixtures.served_version("5.3.0.M5-debian") == "5.3.0.M5"
    assert fixtures.pinned_versions() == {"stable": "5.2.5", "dev": "5.3.0.M5"}


def test_the_client_reads_the_version_from_the_rest_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = OpenHabClient("http://127.0.0.1:8080")
    monkeypatch.setattr(
        client,
        "_request",
        lambda *args, **kwargs: (200, b'{"version": "5.3.0.M5", "locale": "en"}'),
    )
    assert client.server_version() == "5.3.0.M5"


def test_the_client_rejects_a_root_that_is_not_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A port that answers with HTML is not openHAB, whatever the healthcheck says."""
    client = OpenHabClient("http://127.0.0.1:8080")
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: (200, b"<html>"))
    with pytest.raises(OpenHabError) as raised:
        client.server_version()
    assert "did not return JSON" in str(raised.value)


def test_the_running_stable_profile_is_recognised() -> None:
    profile, distances = fixtures.detect_profile(_stack("stable"))
    assert profile == "stable"
    assert distances["stable"] == (0, 0)


def test_the_running_dev_profile_is_recognised() -> None:
    profile, distances = fixtures.detect_profile(_stack("dev"))
    assert profile == "dev"
    assert distances["dev"] == (0, 0)


def test_a_stable_stack_is_not_mistaken_for_dev() -> None:
    """`garmin_confirm` exists only in the dev overlay, so the dev tree is missing it."""
    profile, distances = fixtures.detect_profile(_stack("stable"))
    assert profile == "stable"
    missing_items, missing_sitemaps = distances["dev"]
    assert missing_items == 0
    assert missing_sitemaps >= 1, "the dev overlay adds at least one sitemap"


def test_an_unpinned_version_fails_with_the_observed_and_expected_versions() -> None:
    """A stack from another image is a broken environment, not another profile."""
    with pytest.raises(fixtures.ProfileError) as raised:
        fixtures.detect_profile(_stack("stable", version="5.3.1"))
    message = str(raised.value)
    assert "5.3.1" in message, "the observed version must be in the failure"
    assert "5.2.5" in message and "5.3.0.M5" in message, "both pins must be named"
    assert "pin-images" in message, "the way to accept the new image must be named"


def test_a_rest_api_that_does_not_answer_fails_instead_of_skipping() -> None:
    """`Up (healthy)` with a dead runtime must not read as a profile choice."""
    stack = _OpenHab(error="HTTP 503 Service Unavailable")
    with pytest.raises(fixtures.ProfileError) as raised:
        fixtures.detect_profile(stack)
    message = str(raised.value)
    assert "/rest/" in message
    assert "healthy" in message, "the healthy-looking trap must be named"
    assert "restart" in message
    assert "HTTP 503 Service Unavailable" in message, "the observed answer must survive"


def test_a_port_serving_the_other_profile_is_reported_as_that_profile() -> None:
    stack = _stack("stable")
    profile, _distances = fixtures.detect_profile(stack)
    assert profile == "stable"

    with pytest.raises(fixtures.ProfileMismatch) as raised:
        fixtures.assert_profile(stack, "dev")
    message = str(raised.value)
    assert "asked for the 'dev'" in message
    assert "serves 'stable'" in message
    assert "5.2.5" in message
    assert "COMPOSE_PROFILES=dev" in message, "the start command must be named"
    assert "restart openhab-dev" in message, "the stale-container trap must be named"


def test_the_requested_profile_passes_the_assertion() -> None:
    assert fixtures.assert_profile(_stack("dev"), "dev") == "dev"


def test_a_dev_image_with_a_stable_fixture_tree_fails_loudly() -> None:
    """The version decides the profile; a tree that did not load is a failure."""
    stable = _expected("stable")
    stack = _OpenHab(list(stable.items), list(stable.sitemaps), version="5.3.0.M5")
    with pytest.raises(fixtures.FixtureError) as raised:
        fixtures.detect_profile(stack)
    message = str(raised.value)
    assert "'dev' image" in message
    assert "missing" in message
    assert "openhab-dev-init" in message