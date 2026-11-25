"""The simulator's numeric language index (``Language=7`` is German)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2e.languages import (
    DEFAULT_LANGUAGE,
    LanguageIndexError,
    language_index,
    load_language_index,
    provisionable_languages,
    write_language_index,
)


def _write(path: Path, document: object) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_the_checked_in_map_records_the_measured_german_index() -> None:
    """`Language=7` is the one pair measured on the guest so far."""
    mapping = load_language_index()
    assert mapping.get("deu") == 7


def test_a_missing_file_is_an_empty_map(tmp_path: Path) -> None:
    assert load_language_index(tmp_path / "absent.json") == {}


def test_the_map_round_trips_sorted_by_index(tmp_path: Path) -> None:
    target = tmp_path / "map.json"
    write_language_index({"pol": 9, "deu": 7}, target)
    assert target.read_text(encoding="utf-8") == '{\n  "deu": 7,\n  "pol": 9\n}\n'
    assert load_language_index(target) == {"deu": 7, "pol": 9}


def test_a_bad_map_is_rejected_loudly(tmp_path: Path) -> None:
    with pytest.raises(LanguageIndexError):
        load_language_index(_write(tmp_path / "a.json", ["deu"]))
    with pytest.raises(LanguageIndexError):
        load_language_index(_write(tmp_path / "b.json", {"deu": "7"}))
    with pytest.raises(LanguageIndexError):
        load_language_index(_write(tmp_path / "c.json", {"deu": True}))
    broken = tmp_path / "d.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(LanguageIndexError):
        load_language_index(broken)


def test_an_unmeasured_code_has_no_index() -> None:
    assert language_index("deu", {"deu": 7}) == 7
    assert language_index("pol", {"deu": 7}) is None


def test_the_default_language_is_always_provisionable() -> None:
    """`eng` removes the key and relies on the simulator's own default."""
    assert DEFAULT_LANGUAGE == "eng"
    assert "eng" in provisionable_languages(mapping={})
    assert provisionable_languages(mapping={"deu": 7}) == ("deu", "eng")
    assert provisionable_languages(("eng", "deu", "pol"), mapping={"deu": 7}) == (
        "eng",
        "deu",
    )
    assert provisionable_languages(mapping={"deu": 7}) == ("deu", "eng")
