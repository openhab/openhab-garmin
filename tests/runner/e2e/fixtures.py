"""Fixture expectations and the guard that openHAB actually loaded them.

An openHAB instance whose item and sitemap models were all rejected still
answers HTTP 200 on ``/rest/``. That is how issue #394 stayed invisible: every
scenario "reached" openHAB, but the instance had no items and no file-based
sitemaps, so the first seed failed far away from the real cause.

This module derives the expected sets from the fixture tree itself, compares
them with ``GET /rest/items`` and ``GET /rest/sitemaps``, and, when they do not
match, reports the openHAB ``DSL model ... has errors`` lines so the rejected
file and parser position are in the failure instead of in another container's
log.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple

import yaml

from .config import ConfigError
from .openhab import OpenHabClient, OpenHabError
from .paths import tests_root

PROFILES = ("stable", "dev")

EXPECTED_FILE = "openhab/expected.yaml"
CONF_DIR = "openhab/conf"
DEV_OVERLAY_DIR = "openhab/conf-dev"

FIXTURES_DIR_ENV = "E2E_FIXTURES_DIR"
LOG_DIR_ENV = "E2E_OPENHAB_LOG_DIR"
LOG_ENV = "E2E_OPENHAB_LOG"

#: Mount point of the per-profile userdata volumes in the runner (see
#: tests/compose/compose.yaml). Used only when E2E_OPENHAB_LOG_DIR is unset.
DEFAULT_LOG_DIR = Path("/openhab-logs")

#: Read at most the tail of a log; openHAB logs grow without bound and the
#: model warnings appear during the first seconds of a boot.
LOG_TAIL_BYTES = 4 * 1024 * 1024
LOG_CONTEXT_LINES = 12

#: How long to keep re-reading the REST sets while openHAB finishes loading
#: its models. The healthcheck only proves that ``/rest/`` answers; item and
#: sitemap providers can still be a few seconds behind on a cold start.
DEFAULT_WAIT_S = 30.0
DEFAULT_INTERVAL_S = 2.0

#: Measured on Connect IQ 9.3.0: a 16384-byte JSON response is accepted and a
#: 16385-byte response returns Communications.NETWORK_RESPONSE_TOO_LARGE
#: (-402). See tests/docs/fixture-limits.md. The manifest may override this for
#: a future SDK, but the limit fixtures are checked against it in CI.
SIMULATOR_JSON_LIMIT_BYTES = 16384

#: The declarations this repository's fixtures use. The guard only needs the
#: names, not a full DSL parser; anything it cannot recognise is caught by the
#: expected-set comparison in the other direction.
_ITEM_DECLARATION = re.compile(
    r"^\s*(?P<type>"
    r"Group(?::[A-Za-z]+(?:\([^)]*\))?)*"
    r"|Switch|Number(?::[A-Za-z]+)?|Contact|DateTime|String|Color|Dimmer|Player|Rollershutter"
    r")\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)
_SITEMAP_DECLARATION = re.compile(r"^\s*sitemap\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)")
_LOG_ENTRY = re.compile(r"^\d{2}:\d{2}:\d{2}[ .,]|^\d{4}-\d{2}-\d{2}")
_DSL_WARNING = re.compile(r"DSL model .* has errors")


class FixtureError(ConfigError):
    """openHAB did not load the fixture tree the harness expected."""


@dataclass(frozen=True)
class ExpectedFixtures:
    """The sets a profile's staged fixture tree declares."""

    profile: str
    items: frozenset[str]
    sitemaps: frozenset[str]
    source: Path
    staged_root: Path
    max_sitemap_bytes: Optional[int] = None
    budget_exempt: frozenset[str] = frozenset()
    simulator_json_limit_bytes: int = SIMULATOR_JSON_LIMIT_BYTES
    near_limit_sitemap: Optional[str] = None
    over_limit_sitemap: Optional[str] = None


@dataclass(frozen=True)
class FixtureReport:
    """A successful comparison of expected and actual fixture sets."""

    profile: str
    items: int
    sitemaps: int
    warnings: Tuple[str, ...] = ()
    sitemap_bytes: Tuple[Tuple[str, int], ...] = ()

    def summary(self) -> str:
        return (
            f"{self.items} item(s), {self.sitemaps} sitemap(s), "
            f"profile {self.profile}"
        )


def checkout_fixtures_root() -> Path:
    """``tests/fixtures`` inside the checkout the runner package lives in."""

    return tests_root() / "fixtures"


def staged_fixtures_root() -> Path:
    """The fixture tree Compose mounted into the runner.

    In the stack this is ``/e2e-fixtures``, mounted from
    ``<compose project>/../fixtures`` by ``tests/compose/compose.yaml``. That is
    the same source ``openhab-<profile>-init`` copies into the conf volume, so
    the guard never asserts against a different checkout than openHAB saw.
    Outside the stack it falls back to the checkout's own fixtures.
    """

    value = os.environ.get(FIXTURES_DIR_ENV)
    return Path(value) if value else checkout_fixtures_root()


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _assert_same_tree() -> Path:
    """Fail when the runner and openHAB were pointed at different checkouts.

    ``E2E_REPO_ROOT`` controls the runner's ``/work`` mount, while the staging
    service copies ``../fixtures`` relative to the Compose file. Overriding one
    without the other used to leave the runner asserting against fixtures
    openHAB never loaded; comparing the trees makes that loud.
    """

    staged = staged_fixtures_root()
    if not staged.is_dir():
        raise FixtureError(
            f"fixture tree {staged} does not exist. In the stack, "
            "tests/compose/compose.yaml mounts ../fixtures at /e2e-fixtures; "
            "outside the stack, run from a complete checkout."
        )

    checkout = checkout_fixtures_root()
    if not checkout.is_dir():
        return staged
    try:
        same = staged.resolve() == checkout.resolve()
    except OSError:
        same = False
    if same:
        return staged

    if _tree_digest(staged) != _tree_digest(checkout):
        raise FixtureError(
            "the fixture tree the runner sees "
            f"({staged}) does not match the checkout fixture tree "
            f"({checkout}). E2E_REPO_ROOT points the runner's /work mount at a "
            "different checkout than the Compose project's ../fixtures, so "
            "openHAB staged fixtures the runner is not asserting against. "
            "Start the stack from tests/compose of the checkout under test, or "
            "set E2E_REPO_ROOT to that checkout (and keep ../fixtures in sync)."
        )
    return staged


def _fixture_trees(root: Path, profile: str) -> List[Path]:
    trees = [root / CONF_DIR]
    if profile == "dev":
        overlay = root / DEV_OVERLAY_DIR
        if overlay.is_dir():
            trees.append(overlay)
    return [tree for tree in trees if tree.is_dir()]


def _declared_items(tree: Path) -> Set[str]:
    names: Set[str] = set()
    items_dir = tree / "items"
    if not items_dir.is_dir():
        return names
    for path in sorted(items_dir.glob("*.items")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("#"):
                continue
            match = _ITEM_DECLARATION.match(line)
            if match:
                names.add(match.group("name"))
    return names


def _declared_sitemaps(tree: Path) -> Set[str]:
    names: Set[str] = set()
    sitemaps_dir = tree / "sitemaps"
    if not sitemaps_dir.is_dir():
        return names
    for pattern in ("*.sitemap", "*.sitemaps"):
        for path in sorted(sitemaps_dir.glob(pattern)):
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("//") or stripped.startswith("#"):
                    continue
                match = _SITEMAP_DECLARATION.match(line)
                if match:
                    names.add(match.group("name"))
    return names


def declared_sets(root: Path, profile: str) -> Tuple[Set[str], Set[str]]:
    """The item and sitemap names the staged DSL files declare."""

    items: Set[str] = set()
    sitemaps: Set[str] = set()
    for tree in _fixture_trees(root, profile):
        items.update(_declared_items(tree))
        sitemaps.update(_declared_sitemaps(tree))
    return items, sitemaps


def _string_set(value: object, where: str) -> frozenset[str]:
    if not isinstance(value, list) or not value:
        raise FixtureError(f"{where} must be a non-empty list of names")
    names: List[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise FixtureError(f"{where} entries must be non-empty strings, got {entry!r}")
        names.append(entry.strip())
    if len(set(names)) != len(names):
        raise FixtureError(f"{where} contains duplicate names")
    return frozenset(names)


def load_expected(
    profile: str,
    path: Optional[Path] = None,
    *,
    root: Optional[Path] = None,
) -> ExpectedFixtures:
    """Load the expected sets and check that they match the staged DSL files."""

    if profile not in PROFILES:
        raise FixtureError(f"unknown openHAB profile {profile!r}; known: {', '.join(PROFILES)}")

    staged = Path(root) if root is not None else staged_fixtures_root()
    source = Path(path) if path is not None else staged / EXPECTED_FILE
    if not source.is_file():
        raise FixtureError(
            f"expected fixture manifest {source} is missing. It lists the item "
            "and sitemap sets every scenario relies on; see "
            "tests/fixtures/openhab/expected.yaml."
        )

    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        raise FixtureError(f"{source} is not valid YAML: {error}") from None
    if not isinstance(document, dict):
        raise FixtureError(f"{source} must contain a mapping")
    if document.get("version") != 1:
        raise FixtureError(f"{source} must declare version: 1")

    items = _string_set(document.get("items"), f"{source}: items")
    sitemap_profiles = document.get("sitemaps")
    if not isinstance(sitemap_profiles, dict):
        raise FixtureError(f"{source}: sitemaps must map profile names to lists")
    unknown = sorted(set(sitemap_profiles) - set(PROFILES))
    if unknown:
        raise FixtureError(
            f"{source}: unknown sitemap profile(s) {', '.join(unknown)}; "
            f"known: {', '.join(PROFILES)}"
        )
    if profile not in sitemap_profiles:
        raise FixtureError(f"{source}: no sitemaps entry for profile {profile!r}")
    sitemaps = _string_set(
        sitemap_profiles.get(profile), f"{source}: sitemaps.{profile}"
    )

    max_sitemap_bytes = document.get("max_sitemap_bytes")
    if max_sitemap_bytes is not None:
        if not isinstance(max_sitemap_bytes, int) or max_sitemap_bytes <= 0:
            raise FixtureError(
                f"{source}: max_sitemap_bytes must be a positive integer, "
                f"got {max_sitemap_bytes!r}"
            )
    budget_exempt: frozenset[str] = frozenset()
    raw_exempt = document.get("budget_exempt")
    if raw_exempt is not None:
        budget_exempt = _string_set(raw_exempt, f"{source}: budget_exempt")
        unknown_exempt = sorted(budget_exempt - set(sitemaps))
        if unknown_exempt:
            raise FixtureError(
                f"{source}: budget_exempt names sitemap(s) not in "
                f"sitemaps.{profile}: {_join(unknown_exempt)}"
            )

    simulator_limit = document.get(
        "simulator_json_limit_bytes", SIMULATOR_JSON_LIMIT_BYTES
    )
    if not isinstance(simulator_limit, int) or simulator_limit <= 0:
        raise FixtureError(
            f"{source}: simulator_json_limit_bytes must be a positive integer, "
            f"got {simulator_limit!r}"
        )
    near_limit: Optional[str] = None
    over_limit: Optional[str] = None
    limit_fixtures = document.get("limit_fixtures")
    if limit_fixtures is not None:
        if not isinstance(limit_fixtures, dict):
            raise FixtureError(f"{source}: limit_fixtures must be a mapping")
        near_limit = limit_fixtures.get("near")
        over_limit = limit_fixtures.get("over")
        for role, value in (("near", near_limit), ("over", over_limit)):
            if value is None:
                continue
            if not isinstance(value, str) or value not in sitemaps:
                raise FixtureError(
                    f"{source}: limit_fixtures.{role} must name a sitemap in "
                    f"sitemaps.{profile}, got {value!r}"
                )

    declared_items, declared_sitemaps = declared_sets(staged, profile)
    if declared_items != set(items):
        raise FixtureError(
            f"{source}: items do not match the DSL files under {staged}: "
            f"undeclared in the manifest: {_join(sorted(set(items) - declared_items))}; "
            f"missing from the manifest: {_join(sorted(declared_items - set(items)))}. "
            "Update expected.yaml when adding or renaming an item."
        )
    if declared_sitemaps != set(sitemaps):
        raise FixtureError(
            f"{source}: sitemaps.{profile} does not match the DSL files under {staged}: "
            f"undeclared in the manifest: {_join(sorted(set(sitemaps) - declared_sitemaps))}; "
            f"missing from the manifest: {_join(sorted(declared_sitemaps - set(sitemaps)))}. "
            "Update expected.yaml when adding or renaming a sitemap."
        )

    return ExpectedFixtures(
        profile=profile,
        items=items,
        sitemaps=sitemaps,
        source=source,
        staged_root=staged,
        max_sitemap_bytes=max_sitemap_bytes,
        budget_exempt=budget_exempt,
        simulator_json_limit_bytes=simulator_limit,
        near_limit_sitemap=near_limit,
        over_limit_sitemap=over_limit,
    )


def _join(names: Iterable[str]) -> str:
    values = list(names)
    return ", ".join(values) if values else "(none)"


def candidate_log_paths(
    profile: str,
    log_paths: Sequence[Path] = (),
    log_dir: Optional[Path] = None,
) -> List[Path]:
    """Where to look for the current ``openhab.log``.

    Explicit paths win. Otherwise ``E2E_OPENHAB_LOG`` (one path or an
    ``os.pathsep``-separated list), then ``E2E_OPENHAB_LOG_DIR`` (or the
    runner's ``/openhab-logs`` mount) with the ``<profile>/logs/`` layout the
    Compose stack creates.
    """

    paths: List[Path] = [Path(path) for path in log_paths]
    env_paths = os.environ.get(LOG_ENV)
    if env_paths:
        paths.extend(Path(value) for value in env_paths.split(os.pathsep) if value)

    directory: Optional[Path]
    if log_dir is not None:
        directory = Path(log_dir)
    else:
        value = os.environ.get(LOG_DIR_ENV)
        if value:
            directory = Path(value)
        elif DEFAULT_LOG_DIR.is_dir():
            directory = DEFAULT_LOG_DIR
        else:
            directory = None
    if directory is not None:
        paths.append(directory / profile / "logs" / "openhab.log")
        paths.append(directory / profile / "openhab.log")

    seen: Set[Path] = set()
    unique: List[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def read_dsl_warnings(
    paths: Sequence[Path],
    *,
    context_lines: int = LOG_CONTEXT_LINES,
    tail_bytes: int = LOG_TAIL_BYTES,
) -> Tuple[str, ...]:
    """Extract ``DSL model ... has errors`` blocks from the readable logs."""

    blocks: List[str] = []
    seen: Set[str] = set()
    for path in paths:
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        text = payload[-tail_bytes:].decode("utf-8", errors="replace")
        lines = text.splitlines()
        index = 0
        while index < len(lines):
            line = lines[index]
            if not _DSL_WARNING.search(line):
                index += 1
                continue
            block = [line.strip()]
            following = index + 1
            while following < len(lines) and following <= index + context_lines:
                candidate = lines[following]
                if not candidate.strip() or _LOG_ENTRY.match(candidate.strip()):
                    break
                block.append(candidate.strip())
                following += 1
            rendered = "\n".join(block)
            if rendered not in seen:
                seen.add(rendered)
                blocks.append(rendered)
            index = following
    return tuple(blocks)


def _verification_message(
    expected: ExpectedFixtures,
    *,
    missing_items: Iterable[str],
    unexpected_items: Iterable[str],
    missing_sitemaps: Iterable[str],
    unexpected_sitemaps: Iterable[str],
    warnings: Sequence[str],
) -> str:
    lines = [
        (
            f"openHAB did not load the E2E fixtures for profile {expected.profile!r}."
            if (
                missing_items
                or unexpected_items
                or missing_sitemaps
                or unexpected_sitemaps
            )
            else "openHAB reported a DSL model error even though the expected sets are present."
        )
    ]
    if missing_items:
        lines.append(f"missing item(s): {_join(sorted(missing_items))}")
    if unexpected_items:
        lines.append(f"unexpected item(s): {_join(sorted(unexpected_items))}")
    if missing_sitemaps:
        lines.append(f"missing sitemap(s): {_join(sorted(missing_sitemaps))}")
    if unexpected_sitemaps:
        lines.append(f"unexpected sitemap(s): {_join(sorted(unexpected_sitemaps))}")
    if warnings:
        lines.append("openHAB log:")
        lines.extend(f"  {block}" for block in warnings)
    else:
        lines.append("no openHAB DSL warning could be read; inspect the openHAB log:")
        lines.append(
            "  docker compose -f tests/compose/compose.yaml logs "
            f"openhab-{expected.profile} | grep -F \"DSL model\""
        )
    lines.append(f"fixture tree: {expected.staged_root}")
    lines.append(
        "If that is not the checkout under test, start the stack from its "
        "tests/compose directory (or point E2E_REPO_ROOT at it) so the staging "
        "step and the runner use the same fixture tree."
    )
    return "\n".join(lines)


def verify(
    openhab: OpenHabClient,
    profile: str,
    *,
    expected_path: Optional[Path] = None,
    log_paths: Sequence[Path] = (),
    log_dir: Optional[Path] = None,
    strict: bool = False,
    check_sizes: bool = False,
    dump_dir: Optional[Path] = None,
    wait_s: float = DEFAULT_WAIT_S,
    interval_s: float = DEFAULT_INTERVAL_S,
) -> FixtureReport:
    """Compare the running openHAB with the staged fixture tree.

    The comparison is retried until ``wait_s``, because openHAB answers
    ``/rest/`` before the item and sitemap providers have necessarily finished
    loading a cold start. A permanent mismatch fails with the last observation.

    ``strict`` additionally fails when the sets match but openHAB still logged
    a DSL error. CI uses it on a fresh container; an in-stack run does not,
    because an old boot's warning remains in the userdata log across a restart.

    ``check_sizes`` fetches each sitemap and records its raw REST body size. When
    the manifest sets ``max_sitemap_bytes``, a sitemap above that budget fails
    unless it is listed in ``budget_exempt``. The simulator's network limit is
    measured in tests/docs/fixture-limits.md; a normal fixture must stay well
    below it so a routine element addition does not break every scenario.
    """

    staged = _assert_same_tree()
    expected = load_expected(profile, expected_path, root=staged)
    deadline = time.monotonic() + max(0.0, wait_s)

    while True:
        try:
            actual_items = {item.name for item in openhab.items()}
            actual_sitemaps = set(openhab.sitemaps())
        except OpenHabError as error:
            # /rest/ answers before the REST resources are registered, so a
            # cold start can briefly 404 /rest/items. Treat that exactly like a
            # not-yet-loaded set and keep retrying until the deadline.
            if time.monotonic() >= deadline:
                raise FixtureError(
                    "openHAB did not expose /rest/items and /rest/sitemaps "
                    f"within {wait_s:.0f}s; last error: {error}"
                ) from None
            time.sleep(max(0.1, interval_s))
            continue

        missing_items = expected.items - actual_items
        unexpected_items = actual_items - expected.items
        missing_sitemaps = expected.sitemaps - actual_sitemaps
        unexpected_sitemaps = actual_sitemaps - expected.sitemaps
        set_problems = bool(
            missing_items
            or unexpected_items
            or missing_sitemaps
            or unexpected_sitemaps
        )

        warnings = read_dsl_warnings(
            candidate_log_paths(profile, log_paths=log_paths, log_dir=log_dir)
        )

        if not set_problems:
            if strict and warnings:
                raise FixtureError(
                    _verification_message(
                        expected,
                        missing_items=(),
                        unexpected_items=(),
                        missing_sitemaps=(),
                        unexpected_sitemaps=(),
                        warnings=warnings,
                    )
                )
            sitemap_bytes: Tuple[Tuple[str, int], ...] = ()
            if check_sizes:
                measured = []
                if dump_dir is not None:
                    dump_dir.mkdir(parents=True, exist_ok=True)
                for name in sorted(expected.sitemaps):
                    raw = openhab.sitemap_raw(name)
                    measured.append((name, len(raw)))
                    if dump_dir is not None:
                        (dump_dir / f"{name}.json").write_bytes(raw)
                sitemap_bytes = tuple(measured)
                by_name = dict(measured)
                limit = expected.simulator_json_limit_bytes
                boundary_problems: List[str] = []
                if expected.near_limit_sitemap is not None:
                    size = by_name.get(expected.near_limit_sitemap, 0)
                    if size > limit:
                        boundary_problems.append(
                            f"{expected.near_limit_sitemap}={size} is above the "
                            f"{limit}-byte simulator limit, so the near-limit "
                            "scenario would receive -402"
                        )
                if expected.over_limit_sitemap is not None:
                    size = by_name.get(expected.over_limit_sitemap, 0)
                    if size <= limit:
                        boundary_problems.append(
                            f"{expected.over_limit_sitemap}={size} is not above "
                            f"the {limit}-byte simulator limit, so the over-limit "
                            "scenario would render instead of showing S:-402"
                        )
                if boundary_problems:
                    raise FixtureError(
                        "the size-limit fixtures do not straddle the measured "
                        f"simulator limit: {'; '.join(boundary_problems)}. "
                        "Regenerate them with "
                        "tests/fixtures/openhab/generate_size_fixtures.py and "
                        "record the new sizes in tests/docs/fixture-limits.md."
                    )
                budget = expected.max_sitemap_bytes
                if budget is not None:
                    over = [
                        (name, size)
                        for name, size in measured
                        if size > budget and name not in expected.budget_exempt
                    ]
                    if over:
                        detail = ", ".join(
                            f"{name}={size}" for name, size in over
                        )
                        raise FixtureError(
                            f"sitemap(s) exceed the fixture byte budget "
                            f"({budget} bytes): {detail}. The app's simulator "
                            "rejects a response above 16384 bytes with -402; "
                            "split the sitemap along scenario boundaries instead "
                            "of growing it. See tests/docs/fixture-limits.md."
                        )
            return FixtureReport(
                profile=profile,
                items=len(actual_items),
                sitemaps=len(actual_sitemaps),
                warnings=warnings,
                sitemap_bytes=sitemap_bytes,
            )

        if time.monotonic() >= deadline:
            raise FixtureError(
                _verification_message(
                    expected,
                    missing_items=missing_items,
                    unexpected_items=unexpected_items,
                    missing_sitemaps=missing_sitemaps,
                    unexpected_sitemaps=unexpected_sitemaps,
                    warnings=warnings,
                )
            )
        time.sleep(max(0.1, interval_s))
