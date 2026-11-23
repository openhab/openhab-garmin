"""Build-variant resolution for the device test matrix (issue #366).

The Connect IQ build selects behaviour per device through ``monkey.jungle``:
exclude annotations decide which code is compiled, ``resourcePath`` selects
Edge icon sets, and ``sourcePath`` pulls in device-specific ``Config`` and
``GlanceConfig`` implementations.

This module resolves the jungle the way ``monkeyc`` does -- ``$(name)``
substitution, including references to other device sections such as
``venu3.excludeAnnotations = $(approachs50.excludeAnnotations)``, and the
``base.*`` defaults every device inherits -- and reduces each device to a
tuple of build variants. It then cross-checks the devices against
``manifest.xml`` and against the matrix in ``tests/config/devices.yaml``, so an
uncovered variant fails the unit tests instead of being noticed in a later
regression campaign.

Nothing here needs Docker, the SDK or the simulator: it is plain file parsing
and is exercised by ``tests/runner/tests/test_variants.py``.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, NamedTuple, Optional, Sequence, Tuple

import yaml

from .config import DEVICES_FILE, ConfigError, jungle_path
from .paths import repo_root

#: Variant dimensions, in the order they appear in tables and tags.
DIMENSIONS: Tuple[str, ...] = (
    "ciq_tier",
    "input",
    "screen",
    "menu",
    "edge_resources",
    "settings_entry",
    "config",
)

#: Dimensions whose values the UI tier must exercise on at least one device.
#: ``config`` is deliberately excluded: the protocol tier represents every
#: config variant, and UI goldens for each would multiply runtime without
#: adding an interaction path. See ``tests/docs/devices.md``.
UI_DIMENSIONS: Tuple[str, ...] = (
    "ciq_tier",
    "input",
    "screen",
    "menu",
    "edge_resources",
    "settings_entry",
)

#: A device tag is ``dimension:value`` and is validated by ``config.py``.
TAG = "{dimension}:{value}"

#: Exclude annotations that select a CIQ tier. ``annotCiq510Plus`` also carries
#: ``exclForCiq400Plus``, so the most specific tier is tested first.
_CIQ_ANNOTATIONS: Tuple[Tuple[str, str], ...] = (
    ("exclForCiq510Plus", "510plus"),
    ("exclForCiq400Plus", "400plus"),
    ("exclForCiqPre400", "pre400"),
)

_SETTINGS_ANNOTATIONS: Tuple[Tuple[str, str], ...] = (
    ("exclForSettingsMenuItem", "menu-item"),
    ("exclForSettingsFooter", "footer"),
)

#: Edge resource path marker -> edge_resources value.
_EDGE_RESOURCE_MARKERS: Tuple[Tuple[str, str], ...] = (
    ("resources/edge/icons-interface-short", "short"),
    ("resources/edge/icons-interface-tall", "tall"),
)

#: ``source/config`` directories (without the ``source/config/`` prefix) that
#: identify a device-specific ``Config``/``GlanceConfig`` implementation.
#
# Deliberately explicit: a new config directory is a new build variant and must
# be named here, which forces the matrix to be re-evaluated instead of silently
# gaining a variant the coverage table does not know about.
CONFIG_VARIANTS: Mapping[Tuple[str, ...], str] = {
    ("device-default",): "default",
    ("device-fenix6pro",): "fenix6pro",
    ("device-fr-25deg-select",): "25deg-select",
    ("device-fr-27deg-select",): "27deg-select",
    ("device-no-left-padding",): "no-left-padding",
    ("device-no-left-padding-25deg-select",): "no-left-padding-25deg",
    ("device-no-left-padding-27deg-select",): "no-left-padding-27deg",
    ("device-venu-square/base", "device-venu-square/device-venu2sq"): "venu2sq",
    ("device-venu-square/base", "device-venu-square/device-venux1"): "venux1",
    ("device-edge/base", "device-edge/glance", "device-edge/device-x40-touch"): "edge-x40-touch",
    ("device-edge/base", "device-edge/glance", "device-edge/device-x40-button"): "edge-x40-button",
    ("device-edge/base", "device-edge/glance", "device-edge/device-x50-touch"): "edge-x50-touch",
    ("device-edge/base", "device-edge/glance", "device-edge/device-x50-button/base"): "edge-x50-button",
    (
        "device-edge/base",
        "device-edge/glance",
        "device-edge/device-x50-button/base",
        "device-edge/device-x50-button/device-550",
    ): "edge-x50-550",
    (
        "device-edge/base",
        "device-edge/glance",
        "device-edge/device-x50-button/base",
        "device-edge/device-x50-button/device-850",
    ): "edge-x50-850",
    (
        "device-edge/base",
        "device-edge/glance",
        "device-edge/device-x50-button/base",
        "device-edge/device-1060",
    ): "edge-x50-1060",
}

#: Lookup with config directories sorted, matching the order
#: :func:`_config_variant` produces.
_CONFIG_LOOKUP: Mapping[Tuple[str, ...], str] = {
    tuple(sorted(key)): value for key, value in CONFIG_VARIANTS.items()
}
if len(_CONFIG_LOOKUP) != len(CONFIG_VARIANTS):
    raise RuntimeError("CONFIG_VARIANTS has entries that differ only in directory order")

#: Device properties that the build consumes and that this module resolves.
_DEVICE_PROPERTIES: Tuple[str, ...] = ("excludeAnnotations", "resourcePath", "sourcePath")

_DEVICE_SECTION = re.compile(r"^[a-z0-9]+\.(excludeAnnotations|resourcePath|sourcePath)$")
_VARIABLE = re.compile(r"\$\(([^)]+)\)")


class VariantError(ConfigError):
    """Raised when the build configuration cannot be reduced to variants."""


@dataclass(frozen=True)
class DeviceBuild:
    """One resolved ``monkey.jungle`` device section."""

    product: str
    exclude_annotations: Tuple[str, ...]
    resource_path: Tuple[str, ...]
    source_path: Tuple[str, ...]
    variants: Mapping[str, str]

    @property
    def variant_tags(self) -> Tuple[str, ...]:
        return tuple(TAG.format(dimension=name, value=self.variants[name]) for name in DIMENSIONS)

    @property
    def edge_device(self) -> bool:
        return "resources/edge/icons-interface" in self.resource_path

    def describe(self) -> str:
        return ", ".join(f"{name}={self.variants[name]}" for name in DIMENSIONS)


@dataclass(frozen=True)
class Combination:
    """A distinct variant tuple and the products that build it."""

    variants: Mapping[str, str]
    products: Tuple[str, ...]

    @property
    def variant_tags(self) -> Tuple[str, ...]:
        return tuple(TAG.format(dimension=name, value=self.variants[name]) for name in DIMENSIONS)

    @property
    def label(self) -> str:
        return " / ".join(self.variants[name] for name in DIMENSIONS)


class CoverageProblem(NamedTuple):
    """One reason the device matrix does not cover the build configuration."""

    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.detail}"


# ---------------------------------------------------------------------------
# monkey.jungle
# ---------------------------------------------------------------------------


def parse_jungle(text: str) -> Dict[str, str]:
    """Parse ``key = value`` lines, ignoring ``#`` comments and blank lines."""
    properties: Dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise VariantError(f"monkey.jungle:{number}: expected 'key = value', got {stripped!r}")
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key:
            raise VariantError(f"monkey.jungle:{number}: empty key")
        properties[key] = value.strip()
    return properties


def _substitute(name: str, properties: Mapping[str, str], stack: Tuple[str, ...] = ()) -> str:
    """Expand one property, following ``$(name)`` references recursively."""
    if name in stack:
        chain = " -> ".join(stack + (name,))
        raise VariantError(f"monkey.jungle: circular reference: {chain}")
    try:
        raw = properties[name]
    except KeyError:
        raise VariantError(f"monkey.jungle: unknown property or variable {name!r}") from None

    def replace(match: "re.Match[str]") -> str:
        return _substitute(match.group(1), properties, stack + (name,))

    return _VARIABLE.sub(replace, raw)


def _split(value: str) -> Tuple[str, ...]:
    return tuple(part.strip() for part in value.split(";") if part.strip())


def resolve_device(properties: Mapping[str, str], product: str) -> Dict[str, Tuple[str, ...]]:
    """Resolve one device, applying the ``base.*`` defaults it inherits."""
    resolved: Dict[str, Tuple[str, ...]] = {}
    for prop in _DEVICE_PROPERTIES:
        key = f"{product}.{prop}"
        if key not in properties:
            key = f"base.{prop}"
            if key not in properties:
                raise VariantError(f"monkey.jungle: {product} has no {prop} and base has no default")
        resolved[prop] = _split(_substitute(key, properties))
    return resolved


def jungle_products(properties: Mapping[str, str]) -> Tuple[str, ...]:
    """Every device section in the jungle, sorted by product id."""
    products = {
        match.group(0).split(".", 1)[0]
        for key in properties
        for match in (_DEVICE_SECTION.match(key),)
        if match and not match.group(0).startswith("base.")
    }
    return tuple(sorted(products))


def manifest_products(path: Optional[Path] = None) -> Tuple[str, ...]:
    """Every ``iq:product`` id declared in ``manifest.xml``."""
    manifest = path or repo_root() / "manifest.xml"
    try:
        tree = ElementTree.parse(manifest)
    except (OSError, ElementTree.ParseError) as error:
        raise VariantError(f"cannot read {manifest}: {error}") from None
    products = sorted(
        element.get("id", "")
        for element in tree.iter()
        if element.tag.rsplit("}", 1)[-1] == "product" and element.get("id")
    )
    if not products:
        raise VariantError(f"{manifest}: no iq:product entries")
    return tuple(products)


def _exactly_one(
    present: Sequence[str],
    options: Sequence[Tuple[str, str]],
    product: str,
    what: str,
) -> str:
    found = [value for marker, value in options if marker in present]
    if len(found) != 1:
        raise VariantError(
            f"monkey.jungle: {product} must select exactly one {what}, found {found or 'none'}"
        )
    return found[0]


def _ciq_tier(present: Sequence[str], product: str) -> str:
    # annotCiq510Plus also carries exclForCiq400Plus, so the most specific
    # tier wins instead of requiring exactly one marker.
    for marker, value in _CIQ_ANNOTATIONS:
        if marker in present:
            return value
    raise VariantError(f"monkey.jungle: {product} selects no CIQ tier annotation")


def _config_variant(source_path: Sequence[str], product: str) -> str:
    config_dirs = tuple(
        sorted(
            entry[len("source/config/") :]
            for entry in source_path
            if entry.startswith("source/config/") and entry != "source/config/base"
        )
    )
    try:
        return _CONFIG_LOOKUP[config_dirs]
    except KeyError:
        raise VariantError(
            f"monkey.jungle: {product} uses an unnamed config variant {config_dirs}; "
            "add it to CONFIG_VARIANTS in tests/runner/e2e/variants.py and re-evaluate "
            "the device matrix (see tests/docs/devices.md)"
        ) from None


def variants_for(
    product: str,
    exclude_annotations: Sequence[str],
    resource_path: Sequence[str],
    source_path: Sequence[str],
) -> Dict[str, str]:
    """Reduce one resolved device section to its build-variant tuple."""
    annotations = tuple(exclude_annotations)
    ciq_tier = _ciq_tier(annotations, product)
    settings_entry = _exactly_one(annotations, _SETTINGS_ANNOTATIONS, product, "settings entry")
    input_model = _exactly_one(
        annotations,
        (("exclForTouch", "touch"), ("exclForButton", "button")),
        product,
        "input model",
    )
    screen = _exactly_one(
        annotations,
        (("exclForScreenRound", "round"), ("exclForScreenRectangular", "rectangular")),
        product,
        "screen shape",
    )
    menu = _exactly_one(
        annotations,
        (("exclForActionMenu", "actionmenu"), ("exclForFullMenu", "fullmenu")),
        product,
        "menu implementation",
    )

    edge_resources = "none"
    for marker, value in _EDGE_RESOURCE_MARKERS:
        if marker in resource_path:
            edge_resources = value
            break
    if edge_resources == "none" and any(entry.startswith("resources/edge/") for entry in resource_path):
        raise VariantError(
            f"monkey.jungle: {product} uses Edge resources without a short/tall variant"
        )

    return {
        "ciq_tier": ciq_tier,
        "input": input_model,
        "screen": screen,
        "menu": menu,
        "edge_resources": edge_resources,
        "settings_entry": settings_entry,
        "config": _config_variant(source_path, product),
    }


def load_builds(
    jungle: Optional[Path] = None,
    manifest: Optional[Path] = None,
) -> Tuple[DeviceBuild, ...]:
    """Resolve every device section and cross-check it against ``manifest.xml``."""
    jungle_file = jungle or jungle_path()
    try:
        properties = parse_jungle(jungle_file.read_text(encoding="utf-8"))
    except OSError as error:
        raise VariantError(f"cannot read {jungle_file}: {error}") from None

    declared = set(manifest_products(manifest))
    defined = set(jungle_products(properties))
    missing_sections = sorted(declared - defined)
    undeclared_sections = sorted(defined - declared)
    if missing_sections or undeclared_sections:
        problems = []
        if missing_sections:
            problems.append("in manifest.xml but not monkey.jungle: " + ", ".join(missing_sections))
        if undeclared_sections:
            problems.append("in monkey.jungle but not manifest.xml: " + ", ".join(undeclared_sections))
        raise VariantError("monkey.jungle and manifest.xml disagree; " + "; ".join(problems))

    builds: List[DeviceBuild] = []
    for product in sorted(declared):
        resolved = resolve_device(properties, product)
        builds.append(
            DeviceBuild(
                product=product,
                exclude_annotations=resolved["excludeAnnotations"],
                resource_path=resolved["resourcePath"],
                source_path=resolved["sourcePath"],
                variants=variants_for(
                    product,
                    resolved["excludeAnnotations"],
                    resolved["resourcePath"],
                    resolved["sourcePath"],
                ),
            )
        )
    return tuple(builds)


def combinations(builds: Sequence[DeviceBuild]) -> Tuple[Combination, ...]:
    """Group builds by variant tuple, ordered by tuple for stable output."""
    grouped: Dict[Tuple[str, ...], List[str]] = {}
    for build in builds:
        key = tuple(build.variants[name] for name in DIMENSIONS)
        grouped.setdefault(key, []).append(build.product)
    return tuple(
        Combination(
            variants=dict(zip(DIMENSIONS, key)),
            products=tuple(sorted(grouped[key])),
        )
        for key in sorted(grouped)
    )


# ---------------------------------------------------------------------------
# coverage
# ---------------------------------------------------------------------------


def _tuples_by_product(builds: Sequence[DeviceBuild]) -> Dict[str, Tuple[str, ...]]:
    return {build.product: tuple(build.variants[name] for name in DIMENSIONS) for build in builds}


def _tag_map(tags: Iterable[str]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for tag in tags:
        dimension, _, value = tag.partition(":")
        mapping[dimension] = value
    return mapping


def check_coverage(
    builds: Sequence[DeviceBuild],
    devices: Sequence["DeviceLike"],
    waivers: Sequence["WaiverLike"] = (),
) -> Tuple[CoverageProblem, ...]:
    """Return every way the device matrix fails to cover the build variants.

    A device covers a combination only when its annotated variant tuple equals
    the tuple resolved from ``monkey.jungle``; a partial or stale annotation is
    reported, not ignored. A combination without a device must be matched by a
    waiver with the same full tuple and a reason.
    """
    problems: List[CoverageProblem] = []
    builds_by_product = {build.product: build for build in builds}
    build_tuples = _tuples_by_product(builds)
    known = {tuple(build.variants[name] for name in DIMENSIONS) for build in builds}

    assigned: Dict[Tuple[str, ...], List[str]] = {}
    for device in devices:
        if device.product not in builds_by_product:
            problems.append(
                CoverageProblem(
                    "unknown-product",
                    f"{device.id} uses product {device.product!r}, which is not in monkey.jungle/manifest.xml",
                )
            )
            continue
        tags = _tag_map(device.variants)
        unknown = sorted(set(tags) - set(DIMENSIONS))
        if unknown:
            problems.append(
                CoverageProblem(
                    "unknown-dimension",
                    f"{device.id} uses unknown variant dimension(s): {', '.join(unknown)}",
                )
            )
        missing = [name for name in DIMENSIONS if name not in tags]
        if missing:
            problems.append(
                CoverageProblem(
                    "incomplete-tags",
                    f"{device.id} is missing variant dimension(s): {', '.join(missing)}",
                )
            )
            continue
        tuple_ = tuple(tags[name] for name in DIMENSIONS)
        expected = build_tuples[device.product]
        if tuple_ != expected:
            build = builds_by_product[device.product]
            differences = [
                f"{name}: declared {tags[name]!r}, jungle {build.variants[name]!r}"
                for name in DIMENSIONS
                if tags[name] != build.variants[name]
            ]
            problems.append(
                CoverageProblem(
                    "stale-annotation",
                    f"{device.id} ({device.product}) does not match monkey.jungle: "
                    + "; ".join(differences),
                )
            )
            continue
        assigned.setdefault(tuple_, []).append(device.id)

    waived: set = set()
    for waiver in waivers:
        if not waiver.reason.strip():
            problems.append(CoverageProblem("invalid-waiver", "a waiver needs a reason"))
        tags = _tag_map(waiver.variants)
        missing = [name for name in DIMENSIONS if name not in tags]
        if missing:
            problems.append(
                CoverageProblem(
                    "invalid-waiver",
                    f"waiver for {waiver.reason or '<no reason>'!s} is missing dimension(s): "
                    + ", ".join(missing),
                )
            )
            continue
        tuple_ = tuple(tags[name] for name in DIMENSIONS)
        if tuple_ not in known:
            problems.append(
                CoverageProblem(
                    "stale-waiver",
                    f"waiver for {waiver.reason!r} matches no combination in monkey.jungle",
                )
            )
        elif tuple_ in assigned:
            problems.append(
                CoverageProblem(
                    "unnecessary-waiver",
                    f"waiver for {waiver.reason!r} is no longer needed: "
                    + ", ".join(assigned[tuple_])
                    + " covers it",
                )
            )
        waived.add(tuple_)

    for combination in combinations(builds):
        key = tuple(combination.variants[name] for name in DIMENSIONS)
        if key not in assigned and key not in waived:
            problems.append(
                CoverageProblem(
                    "uncovered",
                    f"{combination.label} ({len(combination.products)} product(s), "
                    f"e.g. {combination.products[0]}) has no device and no waiver",
                )
            )
    return tuple(problems)


class DeviceLike:
    """Structural type of a matrix entry, satisfied by ``config.Device``."""

    id: str
    product: str
    variants: Tuple[str, ...]
    tiers_run: Tuple[str, ...]


def ui_coverage_problems(
    builds: Sequence[DeviceBuild], devices: Sequence[DeviceLike]
) -> Tuple[CoverageProblem, ...]:
    """Check that the UI tier exercises every interaction-relevant value."""
    needed = {
        dimension: sorted({build.variants[dimension] for build in builds})
        for dimension in UI_DIMENSIONS
    }
    covered: Dict[str, set] = {dimension: set() for dimension in UI_DIMENSIONS}
    for device in devices:
        if "ui" not in device.tiers_run:
            continue
        tags = _tag_map(device.variants)
        for dimension in UI_DIMENSIONS:
            if dimension in tags:
                covered[dimension].add(tags[dimension])

    problems = []
    for dimension in UI_DIMENSIONS:
        for value in needed[dimension]:
            if value not in covered[dimension]:
                problems.append(
                    CoverageProblem(
                        "ui-uncovered",
                        f"no UI device covers {dimension}={value}; "
                        "add a device with 'ui' in tiers or document why the value "
                        "needs no visual coverage",
                    )
                )
    return tuple(problems)


class WaiverLike:
    """Structural type of an entry in the top-level ``waivers`` list."""

    variants: Tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class Waiver:
    """A deliberate, explained gap in the device matrix."""

    variants: Tuple[str, ...]
    reason: str
    product: str = ""

    def describe(self) -> str:
        tags = _tag_map(self.variants)
        label = " / ".join(tags.get(name, "?") for name in DIMENSIONS)
        return f"{label} ({self.reason})"


def load_waivers(path: Path = DEVICES_FILE) -> Tuple[Waiver, ...]:
    """Read the top-level ``waivers`` list from ``devices.yaml``."""
    if not path.is_file():
        raise ConfigError(f"missing device matrix: {path}")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    raw_waivers = document.get("waivers", [])
    if raw_waivers is None:
        return ()
    if not isinstance(raw_waivers, list):
        raise ConfigError(f"{path}: 'waivers' must be a list")
    waivers: List[Waiver] = []
    for index, raw in enumerate(raw_waivers):
        where = f"{path}: waivers[{index}]"
        if not isinstance(raw, Mapping):
            raise ConfigError(f"{where}: expected a mapping")
        raw_variants = raw.get("variants")
        if isinstance(raw_variants, (str, bytes)) or not isinstance(raw_variants, (list, tuple)):
            raise ConfigError(f"{where}: 'variants' must be a list of 'dimension:value' tags")
        reason = str(raw.get("reason", "")).strip()
        if not reason:
            raise ConfigError(f"{where}: a waiver needs a 'reason'")
        waivers.append(
            Waiver(
                variants=tuple(str(tag) for tag in raw_variants),
                reason=reason,
                product=str(raw.get("product", "")),
            )
        )
    return tuple(waivers)


def matrix_problems(
    devices_path: Path = DEVICES_FILE,
    jungle: Optional[Path] = None,
    manifest: Optional[Path] = None,
) -> Tuple[CoverageProblem, ...]:
    """Resolve the jungle and return every matrix coverage problem."""
    builds = load_builds(jungle, manifest)
    from .config import load_devices

    devices = load_devices(devices_path)
    return (
        check_coverage(builds, devices, load_waivers(devices_path))
        + ui_coverage_problems(builds, devices)
    )


# ---------------------------------------------------------------------------
# presentation
# ---------------------------------------------------------------------------


def format_combination_table(builds: Sequence[DeviceBuild]) -> str:
    """Human-readable table: one row per distinct variant combination."""
    header = ["CIQ", "input", "screen", "menu", "edge", "settings", "config"]
    rows = [
        [
            combination.variants["ciq_tier"],
            combination.variants["input"],
            combination.variants["screen"],
            combination.variants["menu"],
            combination.variants["edge_resources"],
            combination.variants["settings_entry"],
            combination.variants["config"],
        ]
        + [str(len(combination.products)), combination.products[0]]
        for combination in combinations(builds)
    ]
    columns = header + ["devices", "example"]
    widths = [
        max(len(str(row[index])) for row in [columns] + rows) for index in range(len(columns))
    ]

    def line(values: Sequence[str]) -> str:
        return "| " + " | ".join(str(value).ljust(widths[index]) for index, value in enumerate(values)) + " |"

    separator = "| " + " | ".join("-" * width for width in widths) + " |"
    return "\n".join([line(columns), separator] + [line(row) for row in rows])


def format_matrix_table(
    builds: Sequence[DeviceBuild],
    devices: Sequence[DeviceLike],
    waivers: Sequence[Waiver] = (),
) -> str:
    """Human-readable table: combination -> assigned device(s) or waiver."""
    by_product = _tuples_by_product(builds)
    assigned: Dict[Tuple[str, ...], List[str]] = {}
    for device in devices:
        if device.product not in by_product:
            continue
        tags = _tag_map(device.variants)
        if all(name in tags for name in DIMENSIONS):
            assigned.setdefault(tuple(tags[name] for name in DIMENSIONS), []).append(device.id)
    waived = {
        tuple(_tag_map(waiver.variants).get(name, "?") for name in DIMENSIONS): waiver.reason
        for waiver in waivers
    }

    rows = []
    for combination in combinations(builds):
        key = tuple(combination.variants[name] for name in DIMENSIONS)
        names = sorted(assigned.get(key, ()))
        if names:
            cover = ", ".join(names)
        elif key in waived:
            cover = "waived: " + waived[key]
        else:
            cover = "<uncovered>"
        rows.append(
            [
                combination.variants["ciq_tier"],
                combination.variants["input"],
                combination.variants["screen"],
                combination.variants["menu"],
                combination.variants["edge_resources"],
                combination.variants["settings_entry"],
                combination.variants["config"],
                str(len(combination.products)),
                cover,
            ]
        )
    columns = ["CIQ", "input", "screen", "menu", "edge", "settings", "config", "products", "device"]
    widths = [max(len(str(row[index])) for row in [columns] + rows) for index in range(len(columns))]

    def line(values: Sequence[str]) -> str:
        return "| " + " | ".join(str(value).ljust(widths[index]) for index, value in enumerate(values)) + " |"

    separator = "| " + " | ".join("-" * width for width in widths) + " |"
    return "\n".join([line(columns), separator] + [line(row) for row in rows])


__all__ = [
    "CONFIG_VARIANTS",
    "DIMENSIONS",
    "Combination",
    "CoverageProblem",
    "DeviceBuild",
    "VariantError",
    "Waiver",
    "check_coverage",
    "combinations",
    "format_combination_table",
    "format_matrix_table",
    "jungle_products",
    "load_builds",
    "load_waivers",
    "manifest_products",
    "matrix_problems",
    "parse_jungle",
    "resolve_device",
    "ui_coverage_problems",
    "variants_for",
    "UI_DIMENSIONS",
]
