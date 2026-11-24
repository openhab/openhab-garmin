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
default): the checkout may be mounted read-only, and a build must never mutate
the app source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Tuple

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
    Comments, blank lines and non-device settings are copied verbatim.
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
    kept = []
    for line in lines:
        match = _SECTION_PROPERTY.match(line)
        if match is not None and match.group(1) not in sections:
            continue
        kept.append(line)

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
