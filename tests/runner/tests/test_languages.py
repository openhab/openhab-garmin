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


def test_the_checked_in_map_covers_every_language_the_app_ships() -> None:
    """The table was measured on the guest for indices 0-24 (fenix7, CIQ 6.0.4)."""
    mapping = load_language_index()
    assert mapping["deu"] == 7
    assert {code: mapping[code] for code in ("eng", "deu", "fre", "ita", "spa", "pol")} == {
        "eng": 0,
        "deu": 7,
        "fre": 6,
        "ita": 11,
        "spa": 18,
        "pol": 13,
    }
    # Index 4 also reports eng; the map keeps the first index per code.
    assert 4 not in mapping.values()
    assert sorted(mapping.values()) == [
        0, 1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
        21, 22, 23, 24,
    ]


def test_a_read_only_target_reports_how_to_write_it_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The runner's /work is read-only; a 25-minute scan must not end in a traceback."""
    def refuse(*args: object, **kwargs: object):
        raise OSError(30, "Read-only file system")

    target = tmp_path / "map.json"
    monkeypatch.setattr(Path, "write_text", refuse)
    with pytest.raises(LanguageIndexError) as error:
        write_language_index({"deu": 7}, target)
    message = str(error.value)
    assert "Read-only file system" in message
    assert "--output /tmp/languages.json" in message
    assert "cp /tmp/languages.json" in message


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
