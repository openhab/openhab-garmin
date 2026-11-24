"""Device-scoped ``monkey.jungle`` generation.

``monkey.jungle`` declares resource paths, source paths and annotation
exclusions for every supported device. ``monkeyc`` validates every device
qualifier it finds, so compiling that file needs an SDK whose device database
contains *all* of them. The SDK copy committed to this repository
(``.github/ConnectIQ``) deliberately only holds the devices CI compiles for, and
an operator's SDK may be reduced for the same reason. Against such an SDK the
full jungle fails with::

    ERROR: /work/monkey.jungle:449: 'edge550' is not a valid device / family qualifier.

The repository's CI solves this in ``.github/scripts/monkeyc-build``: it writes
a jungle holding the ``base`` section plus the selected device, follows
``$(device.`` cross references transitively, and drops every other device
section. This module is the harness port of that logic, with the same
semantics, so ``e2e build`` works with any SDK that can compile the device under
test.

The generated file is written outside the checkout (``<artifacts>/build`` by
default) and holds absolute paths rooted at the checkout. The checkout may be
mounted read-only, so the file cannot live next to ``monkey.jungle``, and
``monkeyc`` resolves a relative path in a jungle file against *that file's*
directory — a generated file elsewhere would otherwise look for
``manifest.xml`` beside itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Sequence, Set, Tuple

#: ``monkeyc`` rejects a device qualifier that the SDK's ``api.db`` does not
#: define. The compiler repeats the line for each occurrence; the device name
#: is the only part the operator needs.
INVALID_QUALIFIER = re.compile(r"'([^']+)' is not a valid device / family qualifier")

#: Properties that belong to a device (or to ``base``) section. Only these make
#: a line section-scoped. ``project.manifest`` and ``project.typecheck`` are
#: project settings and must survive: dropping them breaks the build.
_SECTION_PROPERTY = re.compile(
    r"^([a-z][a-z0-9]*)\.(resourcePath|excludeAnnotations|sourcePath)\s*="
)

#: A transitive cross reference such as ``$(approachs50.excludeAnnotations)``.
_REFERENCE = re.compile(r"\$\(([a-z][a-z0-9]*)\.")

#: One ``key = value`` line. ``prefix`` and ``trailing`` keep the original
#: spacing, so a rewrite changes the value only.
_ASSIGNMENT = re.compile(
    r"^(?P<prefix>[A-Za-z_][A-Za-z0-9_.]*\s*=\s*)(?P<value>.*?)(?P<trailing>\s*)$"
)

#: A ``$(name)`` reference inside a value, such as ``$(resLauncherIcons)``.
_VARIABLE_REFERENCE = re.compile(r"\$\(([^)]+)\)")

#: Properties whose values are checkout-relative paths.
_PATH_PROPERTIES = ("resourcePath", "sourcePath")

#: ``project.*`` values that are settings, not paths.
_NON_PATH_PROJECT_VALUES = frozenset({"true", "false"})


class JungleError(RuntimeError):
    """Raised when the source jungle cannot produce a device-scoped file."""


@dataclass(frozen=True)
class DeviceJungle:
    """A generated jungle and the sections it kept."""

    path: Path
    source: Path
    sections: Tuple[str, ...]

    @property
    def transitive(self) -> Tuple[str, ...]:
        """Sections that were added because another section referenced them."""
        return self.sections[2:]


def _property(key: str) -> str:
    """The property part of ``section.property``, or ``""`` for a variable."""
    return key.partition(".")[2] if "." in key else ""


def _path_variable_names(properties: Mapping[str, str]) -> Set[str]:
    """Variables that hold checkout-relative paths, resolved transitively.

    Only variables reachable from a path property, or whose value contains a
    path separator, are rewritten: ``annotCiq510Plus`` and friends are
    annotation names, and prefixing them with the checkout would break
    ``excludeAnnotations``.
    """
    pending: List[str] = []
    for key, value in properties.items():
        if _property(key) in _PATH_PROPERTIES or key == "project.manifest":
            pending.extend(_VARIABLE_REFERENCE.findall(value))
        elif "." not in key and any(
            "/" in entry or "\\" in entry for entry in value.split(";")
        ):
            # A path variable nothing references yet (a dropped section may be
            # its only consumer). It is still a path value in the output.
            pending.append(key)
    names: Set[str] = set()
    while pending:
        name = pending.pop()
        if name in names:
            continue
        names.add(name)
        pending.extend(_VARIABLE_REFERENCE.findall(properties.get(name, "")))
    return names


def _is_path_key(key: str, value: str, path_variables: Set[str]) -> bool:
    if _property(key) in _PATH_PROPERTIES or key == "project.manifest":
        return True
    if key.startswith("project."):
        setting = value.strip().lower()
        return not setting.isdigit() and setting not in _NON_PATH_PROJECT_VALUES
    return "." not in key and key in path_variables


def _absolute_entry(entry: str, root: Path) -> str:
    """Make one ``;``-separated path entry absolute.

    An entry that starts with ``$(name)`` is left alone: the referenced
    variable is rewritten instead, and appending a root would corrupt the
    expansion. A literal suffix after a reference (``$(resLauncherIcons)/40x40``)
    becomes absolute through that variable.
    """
    stripped = entry.strip()
    if not stripped or stripped.startswith("$(") or Path(stripped).is_absolute():
        return stripped
    return str(root / stripped)


def _rewrite_paths(line: str, root: Path, path_variables: Set[str]) -> str:
    """Rewrite a path-valued assignment, preserving everything else verbatim."""
    match = _ASSIGNMENT.match(line)
    if match is None:
        return line
    key = match.group("prefix").split("=", 1)[0].strip()
    value = match.group("value")
    if not _is_path_key(key, value, path_variables):
        return line
    entries = ";".join(_absolute_entry(entry, root) for entry in value.split(";"))
    return f"{match.group('prefix')}{entries}{match.group('trailing')}"


def _required_sections(lines: Sequence[str], device: str) -> List[str]:
    """Resolve ``{base, device}`` plus every transitively referenced section."""
    required = ["base", device]
    known = set(required)
    changed = True
    while changed:
        changed = False
        for line in lines:
            match = _SECTION_PROPERTY.match(line)
            if match is None or match.group(1) not in known:
                continue
            for reference in _REFERENCE.findall(line):
                if reference not in known:
                    known.add(reference)
                    required.append(reference)
                    changed = True
    return required


def generate_device_jungle(source: Path, device: str, output: Path) -> DeviceJungle:
    """Write ``output`` with only the sections ``device`` needs.

    ``base`` is always kept: it carries the defaults every device inherits.
    Device sections that no kept section references are dropped, which is what
    keeps ``monkeyc`` from validating qualifiers the SDK does not define.
    Comments, blank lines and non-path settings are copied verbatim.

    The result is portable. ``monkeyc`` resolves relative jungle paths against
    the jungle file's directory, so a file written outside the checkout would
    look for ``manifest.xml`` and the resource/source trees beside itself.
    Every path-valued line is therefore rewritten to an absolute path rooted at
    ``source``'s directory: ``project.*`` path settings, ``resourcePath`` and
    ``sourcePath`` entries, and the variables those entries reference.
    Annotation values are names, not paths, and stay untouched.
    """
    source = Path(source)
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise JungleError(f"cannot read {source}: {error}") from None

    defined = {
        match.group(1)
        for match in (_SECTION_PROPERTY.match(line) for line in lines)
        if match is not None
    }
    if device not in defined:
        raise JungleError(
            f"{source} has no device section for {device!r}; "
            "add one there, or build for a device the jungle defines"
        )

    sections = _required_sections(lines, device)

    # Resolve path variables from the whole source, not only the kept lines:
    # an unused variable that holds a checkout path is still a path value in
    # the generated file and must not stay relative.
    properties: Mapping[str, str] = {}
    for line in lines:
        match = _ASSIGNMENT.match(line)
        if match is not None:
            key = match.group("prefix").split("=", 1)[0].strip()
            properties[key] = match.group("value")
    root = source.resolve().parent
    path_variables = _path_variable_names(properties)

    kept = []
    for line in lines:
        match = _SECTION_PROPERTY.match(line)
        if match is not None and match.group(1) not in sections:
            continue
        kept.append(_rewrite_paths(line, root, path_variables))

    output = Path(output)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8", newline="\n") as generated:
            for line in kept:
                generated.write(f"{line}\n")
    except OSError as error:
        raise JungleError(f"cannot write {output}: {error}") from None

    return DeviceJungle(path=output, source=source, sections=tuple(sections))


def invalid_qualifiers(output: str) -> Tuple[str, ...]:
    """Device names ``monkeyc`` rejected, in first-seen order."""
    seen: List[str] = []
    for match in INVALID_QUALIFIER.finditer(output):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return tuple(seen)


def qualifier_error(
    device: str,
    qualifiers: Sequence[str],
    jungle: Path,
) -> str:
    """Actionable replacement for a raw ``monkeyc`` qualifier dump.

    ``--jungle`` is deliberately mentioned even when the harness generated the
    small jungle, because the most common cause is a caller passing the full
    one: the SDK then has to define every device the file names.
    """
    names = ", ".join(f"{name!r}" for name in qualifiers) or "a device"
    return (
        f"monkeyc rejected {jungle} while compiling for {device}: {names} "
        "is not a valid device / family qualifier.\n"
        "The SDK's device set is narrower than the jungle: it does not define "
        "that device, so it cannot validate the line naming it.\n"
        "Build for a device the SDK provides with `--device <id>`; with a "
        "reduced SDK keep the default generated jungle and do not pass "
        "`--jungle`. A complete SDK is only needed when the full jungle is "
        "used deliberately. See tests/docs/sdk.md (Device-scoped jungle)."
    )
