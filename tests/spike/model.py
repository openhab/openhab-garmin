"""Shared check/result model for the Wine spike.

The in-container probe writes one JSON document built from :class:`Check`
records. The host reads it back and computes the #370 verdict. Keeping the
schema here means the probe, the host and the unit tests cannot drift: a check
id is a plain string, but its status is validated on construction and on load.

Status vocabulary is the one #370 uses for its pass criteria:

``met``
    Observed and verified, with evidence.
``not-met``
    Attempted and not observed (or observed to be wrong).
``skipped``
    Not evaluable in this environment. A skipped check is never a pass -- the
    same rule the E2E harness applies to skipped assertions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

MET = "met"
NOT_MET = "not-met"
SKIPPED = "skipped"

STATUSES = (MET, NOT_MET, SKIPPED)


class SpikeError(RuntimeError):
    """Raised when spike input or probe output is malformed."""


def validate_status(status: str, where: str = "check") -> str:
    """Return ``status`` or raise when it is not part of the vocabulary."""
    if status not in STATUSES:
        raise SpikeError(
            f"{where}: unknown status {status!r}; expected one of {', '.join(STATUSES)}"
        )
    return status


@dataclass
class Check:
    """One observed fact used by the verdict.

    ``evidence`` holds artifact paths relative to the spike artifacts directory
    (screenshots, console logs, JSON dumps). ``measurements`` holds numeric
    observations (milliseconds, counts) that the verdict summary reports.
    """

    id: str
    status: str
    detail: str = ""
    evidence: List[str] = field(default_factory=list)
    measurements: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise SpikeError("check id must be a non-empty string")
        validate_status(self.status, where=f"check {self.id}")
        if not isinstance(self.detail, str):
            raise SpikeError(f"check {self.id}: detail must be a string")
        self.evidence = [str(item) for item in self.evidence]
        for key, value in self.measurements.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SpikeError(
                    f"check {self.id}: measurement {key!r} must be a number, "
                    f"got {value!r}"
                )
        self.measurements = {str(key): float(value) for key, value in self.measurements.items()}

    @property
    def met(self) -> bool:
        return self.status == MET

    def to_document(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "detail": self.detail,
            "evidence": list(self.evidence),
            "measurements": dict(sorted(self.measurements.items())),
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "Check":
        try:
            check_id = str(document["id"])
        except KeyError:
            raise SpikeError("check document has no 'id'") from None
        evidence = document.get("evidence") or []
        if isinstance(evidence, (str, bytes)) or not isinstance(evidence, Iterable):
            raise SpikeError(f"check {check_id}: 'evidence' must be a list")
        measurements = document.get("measurements") or {}
        if not isinstance(measurements, Mapping):
            raise SpikeError(f"check {check_id}: 'measurements' must be a mapping")
        return cls(
            id=check_id,
            status=str(document.get("status", SKIPPED)),
            detail=str(document.get("detail", "")),
            evidence=list(evidence),
            measurements=dict(measurements),
        )


class CheckLog:
    """Collect checks by id while the probe works through the procedure."""

    def __init__(self) -> None:
        self._checks: Dict[str, Check] = {}

    def record(
        self,
        check_id: str,
        status: str,
        detail: str = "",
        evidence: Iterable[str] = (),
        measurements: Optional[Mapping[str, float]] = None,
    ) -> Check:
        """Record a check, replacing any earlier record with the same id."""
        check = Check(
            id=check_id,
            status=status,
            detail=detail,
            evidence=list(evidence),
            measurements=dict(measurements or {}),
        )
        self._checks[check.id] = check
        return check

    def skip(self, check_id: str, detail: str, **kwargs: Any) -> Check:
        return self.record(check_id, SKIPPED, detail, **kwargs)

    def get(self, check_id: str) -> Optional[Check]:
        return self._checks.get(check_id)

    def __contains__(self, check_id: object) -> bool:
        return check_id in self._checks

    def __len__(self) -> int:
        return len(self._checks)

    @property
    def checks(self) -> List[Check]:
        return list(self._checks.values())

    def to_documents(self) -> List[Dict[str, Any]]:
        return [check.to_document() for check in self.checks]


@dataclass
class ProbeDocument:
    """What the in-container probe writes and the host reads.

    ``prerequisites`` is non-empty only when the probe refused to start. The
    host turns that into a single error message naming everything that is
    missing, before any verdict is computed.
    """

    checks: List[Check] = field(default_factory=list)
    prerequisites: List[str] = field(default_factory=list)
    environment: Dict[str, Any] = field(default_factory=dict)
    artifacts_root: str = ""

    @classmethod
    def from_checks(
        cls,
        checks: Iterable[Check],
        *,
        prerequisites: Iterable[str] = (),
        environment: Optional[Mapping[str, Any]] = None,
        artifacts_root: str = "",
    ) -> "ProbeDocument":
        return cls(
            checks=list(checks),
            prerequisites=[str(item) for item in prerequisites],
            environment=dict(environment or {}),
            artifacts_root=artifacts_root,
        )

    def to_document(self) -> Dict[str, Any]:
        return {
            "artifacts_root": self.artifacts_root,
            "environment": dict(sorted(self.environment.items())),
            "prerequisites": list(self.prerequisites),
            "checks": [check.to_document() for check in self.checks],
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "ProbeDocument":
        if not isinstance(document, Mapping):
            raise SpikeError("probe document must be a JSON object")
        raw_checks = document.get("checks") or []
        if not isinstance(raw_checks, list):
            raise SpikeError("probe document 'checks' must be a list")
        prerequisites = document.get("prerequisites") or []
        if isinstance(prerequisites, (str, bytes)) or not isinstance(prerequisites, list):
            raise SpikeError("probe document 'prerequisites' must be a list")
        environment = document.get("environment") or {}
        if not isinstance(environment, Mapping):
            raise SpikeError("probe document 'environment' must be a mapping")
        return cls(
            checks=[Check.from_document(item) for item in raw_checks],
            prerequisites=[str(item) for item in prerequisites],
            environment=dict(environment),
            artifacts_root=str(document.get("artifacts_root", "")),
        )

    def check_map(self) -> Dict[str, Check]:
        return {check.id: check for check in self.checks}


def parse_console_line(line: str) -> Optional[Dict[str, str]]:
    """Parse one ``E2E:`` trace line without importing the harness.

    The probe runs before any verdict and should stay standard-library only,
    so this is a deliberately small subset of ``e2e.trace``: split on ``;`` and
    each part on the first ``=``. Unknown events are kept; the probe only looks
    for ``VIEW``, ``CONNECTION``, ``SITEMAP``, ``ERRORVIEW`` and ``LANGUAGE``.
    """
    text = line.strip()
    marker = text.find("E2E:")
    if marker < 0:
        return None
    payload = text[marker + len("E2E:"):].strip()
    if not payload:
        return None
    parts = payload.split(";")
    event = parts[0].strip()
    if not event or not event[0].isalpha():
        return None
    fields: Dict[str, str] = {}
    for part in parts[1:]:
        key, separator, value = part.partition("=")
        if separator and key.strip():
            fields[key.strip()] = value.strip()
    return {"event": event, "raw": text, **fields}
