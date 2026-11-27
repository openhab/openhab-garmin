"""Profile detection: which fixture tree the running openHAB actually serves.

Profiles are exclusive — one openHAB serves one tree — so a full run needs one
pass per profile. These tests pin the detection rule that lets the engine skip a
scenario written for another profile instead of failing it in the guard.
"""

from __future__ import annotations

from typing import List, Sequence

from e2e import fixtures


class _Item:
    def __init__(self, name: str) -> None:
        self.name = name


class _OpenHab:
    def __init__(self, items: Sequence[str], sitemaps: Sequence[str]) -> None:
        self._items = [_Item(name) for name in items]
        self._sitemaps = list(sitemaps)

    def items(self) -> List[_Item]:
        return self._items

    def sitemaps(self) -> List[str]:
        return self._sitemaps


def _expected(profile: str):
    return fixtures.load_expected(profile)


def test_the_running_stable_profile_is_recognised() -> None:
    stable = _expected("stable")
    profile, distances = fixtures.detect_profile(
        _OpenHab(list(stable.items), list(stable.sitemaps))
    )
    assert profile == "stable"
    assert distances["stable"] == (0, 0)


def test_the_running_dev_profile_is_recognised() -> None:
    dev = _expected("dev")
    profile, distances = fixtures.detect_profile(
        _OpenHab(list(dev.items), list(dev.sitemaps))
    )
    assert profile == "dev"
    assert distances["dev"] == (0, 0)


def test_a_stable_stack_is_not_mistaken_for_dev() -> None:
    """`garmin_confirm` exists only in the dev overlay, so the dev tree is missing it."""
    stable = _expected("stable")
    profile, distances = fixtures.detect_profile(
        _OpenHab(list(stable.items), list(stable.sitemaps))
    )
    assert profile == "stable"
    missing_items, missing_sitemaps = distances["dev"]
    assert missing_items == 0
    assert missing_sitemaps >= 1, "the dev overlay adds at least one sitemap"


def test_an_empty_instance_matches_no_profile() -> None:
    profile, distances = fixtures.detect_profile(_OpenHab([], []))
    assert profile is None
    assert all(missed[0] > 0 for missed in distances.values())
