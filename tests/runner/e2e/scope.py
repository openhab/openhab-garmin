"""Regression scope definition for issue #140.

The issue is prose; this module turns it into a stable, machine-readable list
of scope entries. A scenario declares which entries it covers with a ``covers``
list, and :mod:`e2e.coverage` turns the two into ``tests/docs/coverage.md``.

The file lives at ``tests/config/scope.yaml``. Its schema is deliberately
small: sections, entries and waivers. A waiver is a deliberate, explained gap;
an entry with neither a scenario nor a waiver fails the coverage check.

No Docker, SDK or simulator is needed to read it, which keeps the check in the
``harness`` CI job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import yaml

from .config import SCOPE_FILE, ConfigError


class ScopeError(ConfigError):
    """Raised when ``scope.yaml`` does not follow its schema."""


_SECTION_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ENTRY_ID = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")

#: Parsed files, keyed by path and stat identity, so scenario discovery does
#: not re-read the same 17 KiB YAML once per scenario.
_CACHE: Dict[Tuple[str, int, int], "Scope"] = {}


@dataclass(frozen=True)
class ScopeSection:
    """A top-level section of the issue."""

    id: str
    title: str
    number: str

    @property
    def label(self) -> str:
        return f"{self.number}. {self.title}" if self.number else self.title


@dataclass(frozen=True)
class ScopeEntry:
    """One bullet of the issue."""

    id: str
    section: str
    title: str


@dataclass(frozen=True)
class ScopeWaiver:
    """A deliberate, explained entry with no scenario."""

    id: str
    reason: str
    verified_by: str = ""


@dataclass(frozen=True)
class ScopeBlocked:
    """An entry the environment cannot exercise until a tracked issue is fixed.

    Unlike a waiver (a reviewed decision not to automate), a blocked entry is
    expected to become coverable: the harness is not the reason it is missing.
    ``issue`` points at the limitation, so the rendered document links to it.
    """

    id: str
    reason: str
    issue: int


@dataclass(frozen=True)
class Scope:
    """The validated scope document."""

    version: int
    issue: int
    source: str
    sections: Tuple[ScopeSection, ...]
    entries: Tuple[ScopeEntry, ...]
    waivers: Tuple[ScopeWaiver, ...]
    blocked: Tuple[ScopeBlocked, ...] = ()

    @property
    def entry_ids(self) -> Tuple[str, ...]:
        return tuple(entry.id for entry in self.entries)

    def section(self, section_id: str) -> ScopeSection:
        for section in self.sections:
            if section.id == section_id:
                return section
        raise KeyError(section_id)

    def issue_url(self, number: int) -> str:
        """The URL of a related issue, derived from this document's source."""
        base = self.source.rsplit("/issues/", 1)[0]
        return f"{base}/issues/{number}"


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ScopeError(f"{where}: expected a mapping, got {type(value).__name__}")
    return value


def _unknown(mapping: Mapping[str, Any], allowed: Sequence[str], where: str) -> None:
    unknown = sorted(set(mapping) - set(allowed))
    if unknown:
        raise ScopeError(f"{where}: unknown key(s): {', '.join(unknown)}")


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise ScopeError(f"{where}: expected a string, got {type(value).__name__}")
    text = value.strip()
    if not text:
        raise ScopeError(f"{where}: must not be empty")
    return text


def _list(value: Any, where: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ScopeError(f"{where}: expected a list")
    return value


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def _parse(document: Any, source: Path) -> Scope:
    mapping = _mapping(document, str(source))
    _unknown(
        mapping,
        ("version", "issue", "source", "sections", "entries", "waivers", "blocked"),
        str(source),
    )

    version = mapping.get("version")
    if version != 1:
        raise ScopeError(f"{source}: version must be 1, got {version!r}")

    issue = mapping.get("issue")
    if isinstance(issue, bool) or not isinstance(issue, int):
        raise ScopeError(f"{source}: 'issue' must be an integer")

    raw_sections = _list(mapping.get("sections"), f"{source}: sections")
    if not raw_sections:
        raise ScopeError(f"{source}: 'sections' must not be empty")
    sections = []
    section_ids = set()
    for index, raw in enumerate(raw_sections):
        where = f"{source}: sections[{index}]"
        item = _mapping(raw, where)
        _unknown(item, ("id", "title", "number"), where)
        section_id = _text(item.get("id"), f"{where}.id")
        if not _SECTION_ID.match(section_id):
            raise ScopeError(f"{where}.id: {section_id!r} must be kebab-case")
        if section_id in section_ids:
            raise ScopeError(f"{where}.id: duplicate section {section_id!r}")
        section_ids.add(section_id)
        number = item.get("number", "")
        if number is not None and not isinstance(number, (str, int)):
            raise ScopeError(f"{where}.number: expected a string or integer")
        sections.append(
            ScopeSection(
                id=section_id,
                title=_text(item.get("title"), f"{where}.title"),
                number=str(number) if number is not None else "",
            )
        )

    raw_entries = _list(mapping.get("entries"), f"{source}: entries")
    if not raw_entries:
        raise ScopeError(f"{source}: 'entries' must not be empty")
    entries = []
    entry_ids = set()
    for index, raw in enumerate(raw_entries):
        where = f"{source}: entries[{index}]"
        item = _mapping(raw, where)
        _unknown(item, ("id", "section", "title"), where)
        entry_id = _text(item.get("id"), f"{where}.id")
        if not _ENTRY_ID.match(entry_id):
            raise ScopeError(
                f"{where}.id: {entry_id!r} must be lower-case dotted/kebab-case"
            )
        if entry_id in entry_ids:
            raise ScopeError(f"{where}.id: duplicate scope id {entry_id!r}")
        entry_ids.add(entry_id)
        section = _text(item.get("section"), f"{where}.section")
        if section not in section_ids:
            raise ScopeError(f"{where}.section: unknown section {section!r}")
        entries.append(
            ScopeEntry(
                id=entry_id,
                section=section,
                title=_text(item.get("title"), f"{where}.title"),
            )
        )

    raw_waivers = mapping.get("waivers", [])
    if raw_waivers is None:
        raw_waivers = []
    waivers = []
    waived = set()
    for index, raw in enumerate(_list(raw_waivers, f"{source}: waivers")):
        where = f"{source}: waivers[{index}]"
        item = _mapping(raw, where)
        _unknown(item, ("id", "reason", "verified_by"), where)
        entry_id = _text(item.get("id"), f"{where}.id")
        if entry_id not in entry_ids:
            raise ScopeError(f"{where}.id: unknown scope id {entry_id!r}")
        if entry_id in waived:
            raise ScopeError(f"{where}.id: duplicate waiver for {entry_id!r}")
        waived.add(entry_id)
        verified_by = item.get("verified_by", "")
        if verified_by is not None and not isinstance(verified_by, str):
            raise ScopeError(f"{where}.verified_by: expected a string")
        waivers.append(
            ScopeWaiver(
                id=entry_id,
                reason=_text(item.get("reason"), f"{where}.reason"),
                verified_by=(verified_by or "").strip(),
            )
        )

    raw_blocked = mapping.get("blocked", [])
    if raw_blocked is None:
        raw_blocked = []
    blocked = []
    blocked_ids = set()
    for index, raw in enumerate(_list(raw_blocked, f"{source}: blocked")):
        where = f"{source}: blocked[{index}]"
        item = _mapping(raw, where)
        _unknown(item, ("id", "reason", "issue"), where)
        entry_id = _text(item.get("id"), f"{where}.id")
        if entry_id not in entry_ids:
            raise ScopeError(f"{where}.id: unknown scope id {entry_id!r}")
        if entry_id in blocked_ids:
            raise ScopeError(f"{where}.id: duplicate blocked entry for {entry_id!r}")
        if entry_id in waived:
            raise ScopeError(
                f"{where}.id: {entry_id!r} cannot be both blocked and waived"
            )
        number = item.get("issue")
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise ScopeError(f"{where}.issue: expected a positive issue number")
        blocked_ids.add(entry_id)
        blocked.append(
            ScopeBlocked(
                id=entry_id,
                reason=_text(item.get("reason"), f"{where}.reason"),
                issue=number,
            )
        )

    return Scope(
        version=version,
        issue=issue,
        source=_text(mapping.get("source"), f"{source}: source"),
        sections=tuple(sections),
        entries=tuple(entries),
        waivers=tuple(waivers),
        blocked=tuple(blocked),
    )


def load_scope(path: Path = SCOPE_FILE) -> Scope:
    """Load and validate ``tests/config/scope.yaml``."""
    if not path.is_file():
        raise ScopeError(f"missing scope definition: {path}")
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ScopeError(f"{path}: invalid YAML: {error}") from None
    scope = _parse(document, path)
    _CACHE[key] = scope
    return scope


def scope_ids(path: Path = SCOPE_FILE) -> Tuple[str, ...]:
    """Every stable scope id, in document order."""
    return load_scope(path).entry_ids


def find_unknown_ids(
    scope: Scope,
    ids: Sequence[str],
) -> Tuple[str, ...]:
    """Return the ids that do not exist in ``scope``, sorted and deduplicated."""
    known = set(scope.entry_ids)
    return tuple(sorted({entry_id for entry_id in ids if entry_id not in known}))


def describe_entry(scope: Scope, entry_id: str) -> Optional[str]:
    """Return ``"<section number>. <section> - <title>"`` for messages."""
    for entry in scope.entries:
        if entry.id == entry_id:
            section = scope.section(entry.section)
            return f"{section.label}: {entry.title}"
    return None
