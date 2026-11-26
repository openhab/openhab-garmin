"""Assertions used by scenario steps.

Two families:

- **Trace assertions** on the shim's request log. These are exact and are the
  primary signal for protocol-tier scenarios: "the app sent exactly one POST to
  this item with this body" is stronger than any screenshot.
- **Golden-image assertions** for UI-tier scenarios. They compare a screenshot
  with a reviewed baseline and are allowed a small pixel-difference ratio so
  that anti-aliasing differences between SDK versions do not create noise.

Failures raise :class:`AssertionFailure`, which the engine records as a failed
step with the actual trace attached.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Sequence, Tuple

from .shim import ShimRequest

#: Tolerated fraction of differing pixels for golden comparisons.
DEFAULT_MAX_DIFF_RATIO = 0.02


class AssertionFailure(AssertionError):
    """A scenario assertion did not hold."""


def filter_requests(
    requests: Iterable[ShimRequest],
    method: Optional[str] = None,
    path: Optional[str] = None,
    path_prefix: Optional[str] = None,
    body_contains: Optional[str] = None,
    body_equals: Optional[str] = None,
    status: Optional[int] = None,
    reset: Optional[bool] = None,
) -> List[ShimRequest]:
    """Return the recorded requests that satisfy every given condition."""
    matched: List[ShimRequest] = []
    for request in requests:
        if method is not None and request.method.upper() != method.upper():
            continue
        if path is not None and request.path != path:
            continue
        if path_prefix is not None and not request.path.startswith(path_prefix):
            continue
        if body_contains is not None and body_contains not in request.body:
            continue
        if body_equals is not None and request.body != body_equals:
            continue
        if status is not None and request.status != status:
            continue
        if reset is not None and request.reset != reset:
            continue
        matched.append(request)
    return matched


def format_requests(requests: Sequence[ShimRequest], limit: int = 12) -> str:
    """Render a request trace for failure messages."""
    if not requests:
        return "<no requests recorded>"
    shown = list(requests)[-limit:]
    return "\n".join("  " + request.describe() for request in shown)


def assert_requests(
    requests: Sequence[ShimRequest],
    spec: Mapping[str, object],
    description: str = "",
) -> int:
    """Assert a count over the requests that match ``spec``.

    Returns the matched count. Raises :class:`AssertionFailure` with both the
    expectation and the full trace, because "expected 2, found 0" on its own is
    not enough to debug a failing harness.
    """
    matched = filter_requests(requests, **{key: spec[key] for key in spec
                                           if key in {"method", "path", "path_prefix",
                                                      "body_contains", "body_equals",
                                                      "status", "reset"}})
    count = len(matched)
    minimum = spec.get("min_count")
    maximum = spec.get("max_count")

    def failure(reason: str) -> AssertionFailure:
        head = description or "shim request assertion"
        return AssertionFailure(
            f"{head}\n  {reason}\n  matched {count} request(s)\n"
            f"  full trace:\n{format_requests(requests)}"
        )

    if isinstance(minimum, int) and count < minimum:
        raise failure(f"expected at least {minimum} matching request(s)")
    if isinstance(maximum, int) and count > maximum:
        raise failure(f"expected at most {maximum} matching request(s)")
    return count


# ---------------------------------------------------------------------------
# golden images
# ---------------------------------------------------------------------------


def _imagemagick(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        raise AssertionFailure(
            f"ImageMagick '{tool}' is not on PATH. The runner image installs it; "
            "on a workstation install ImageMagick or run inside the container."
        )
    return path


def _run(command: Sequence[str]) -> Tuple[int, str, str]:
    completed = subprocess.run(  # noqa: S603 - fixed argument list
        list(command), capture_output=True, text=True
    )
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def image_size(path: Path) -> Tuple[int, int]:
    """Return ``(width, height)`` of an image using ImageMagick ``identify``."""
    code, out, err = _run([_imagemagick("identify"), "-format", "%w %h\n", str(path)])
    if code != 0:
        raise AssertionFailure(f"cannot read {path}: {err.strip() or out.strip()}")
    first = out.strip().splitlines()[0] if out.strip() else ""
    try:
        width, height = (int(part) for part in first.split())
    except ValueError:
        raise AssertionFailure(f"cannot parse image size for {path}: {first!r}") from None
    return width, height


def diff_pixels(actual: Path, golden: Path) -> int:
    """Number of differing pixels between two images (``compare -metric AE``)."""
    code, _out, err = _run([
        _imagemagick("compare"),
        "-metric",
        "AE",
        str(actual),
        str(golden),
        "null:",
    ])
    # compare exits 1 when the images differ; 2 means it could not compare.
    if code not in (0, 1):
        raise AssertionFailure(f"compare failed for {actual} vs {golden}: {err.strip()}")
    text = err.strip().split()[0] if err.strip() else "0"
    try:
        return int(float(text))
    except ValueError:
        raise AssertionFailure(f"cannot parse compare output {err.strip()!r}") from None


def assert_golden(
    actual: Path,
    golden: Path,
    max_diff_ratio: float = DEFAULT_MAX_DIFF_RATIO,
) -> None:
    """Compare a screenshot with its reviewed baseline."""
    if not actual.is_file():
        raise AssertionFailure(f"screenshot {actual} does not exist")
    if not golden.is_file():
        raise AssertionFailure(
            f"no golden image at {golden}. A golden must be captured and "
            "reviewed on a Windows host in the Connect IQ simulator: this "
            "simulator build under Wine cannot paint app text, so "
            "`--update-goldens` is refused there and a capture is a crashed "
            "render. See tests/docs/visual-truth.md."
        )
    width, height = image_size(actual)
    golden_width, golden_height = image_size(golden)
    if (width, height) != (golden_width, golden_height):
        raise AssertionFailure(
            f"size mismatch: {actual} is {width}x{height}, "
            f"{golden} is {golden_width}x{golden_height}"
        )
    differing = diff_pixels(actual, golden)
    ratio = differing / float(width * height) if width * height else 0.0
    if ratio > max_diff_ratio:
        raise AssertionFailure(
            f"{differing} of {width * height} pixels differ ({ratio:.4%}) between "
            f"{actual} and {golden}; allowed {max_diff_ratio:.2%}. "
            "Review the screenshot, then re-capture the golden if the change is intended."
        )


def update_golden(actual: Path, golden: Path) -> None:
    """Replace a golden image with ``actual`` (``--update-goldens``)."""
    if not actual.is_file():
        raise AssertionFailure(f"screenshot {actual} does not exist")
    golden.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(actual, golden)
