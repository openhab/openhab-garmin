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
from typing import Callable, Iterable, List, Mapping, Optional, Sequence, Tuple

from .shim import ShimRequest

#: Tolerated fraction of differing pixels for golden comparisons. A whole-window
#: golden includes the simulator's status strip, whose live counters tick: the
#: guest measured 157 differing pixels (0.06%) between two captures of the same
#: view with the pointer parked, and 466 (0.19%) when the pointer moved. A wrong
#: view (glance instead of the sitemap menu) differs by ~1.6%, so 0.2% tolerates
#: the strip without accepting the failure this default used to hide.
DEFAULT_MAX_DIFF_RATIO = 0.002

#: A pixel this far from the image's modal background counts as ink for
#: :func:`screen_content_problem` (0-255 scale).
INK_THRESHOLD = 40

#: Ink components smaller than this are noise, not glyphs.
INK_MIN_COMPONENT_PX = 3

#: How much two consecutive captures of a settled screen may differ, as a
#: fraction of the image, before the screen is considered still painting. The
#: simulator's status strip drifts by ~0.06%, so this must stay above that and
#: well below the difference between two different views.
DEFAULT_STABLE_DIFF_RATIO = 0.001


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
            f"no golden image at {golden}. Capture one with `--update-goldens`, "
            "review it, and commit it."
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


# ---------------------------------------------------------------------------
# content guard: never lock in a screen that shows nothing
# ---------------------------------------------------------------------------

def parse_pgm(data: bytes) -> Optional[Tuple[int, int, bytes]]:
    """Decode a binary (P5) PGM image into ``(width, height, pixels)``."""
    if not data.startswith(b"P5"):
        return None
    fields = data.split(b"\n", 3)
    if len(fields) != 4:
        return None
    try:
        width, height = (int(part) for part in fields[1].split())
        maxval = int(fields[2])
    except ValueError:
        return None
    pixels = fields[3]
    if width <= 0 or height <= 0 or maxval != 255 or len(pixels) < width * height:
        return None
    return width, height, pixels


def modal_value(pixels: bytes) -> int:
    """The most common byte value, i.e. the image's background."""
    counts = [0] * 256
    for value in pixels:
        counts[value] += 1
    return max(range(256), key=lambda value: counts[value])


def ink_components(
    width: int,
    height: int,
    pixels: bytes,
    *,
    threshold: int = INK_THRESHOLD,
    min_size: int = INK_MIN_COMPONENT_PX,
) -> int:
    """Number of connected ink blobs at least ``min_size`` pixels large.

    Text is many small blobs; an idle watch face is a handful of large ones. The
    count works on the whole image, so it makes no assumption about where the
    app's screen sits inside the simulator window (it is not always centred, and
    the latest bike computers crop the device's top).
    """
    background = modal_value(pixels)
    seen = bytearray(width * height)
    count = 0
    for start_y in range(height):
        for start_x in range(width):
            index = start_y * width + start_x
            if seen[index]:
                continue
            seen[index] = 1
            if abs(pixels[index] - background) <= threshold:
                continue
            stack = [(start_x, start_y)]
            size = 0
            while stack:
                x, y = stack.pop()
                size += 1
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < width and 0 <= ny < height:
                        neighbour = ny * width + nx
                        if not seen[neighbour] and abs(pixels[neighbour] - background) > threshold:
                            seen[neighbour] = 1
                            stack.append((nx, ny))
            if size >= min_size:
                count += 1
    return count


def _pgm_stdout(command: Sequence[str]) -> Optional[bytes]:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argument list
            list(command), capture_output=True, timeout=120
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout or b""


def image_ink_components(
    path: Path,
    *,
    runner: Optional[Callable[[Sequence[str]], Optional[bytes]]] = None,
) -> Optional[int]:
    """Ink-component count for ``path``, or ``None`` without ImageMagick."""
    convert = shutil.which("convert")
    if convert is None:
        return None
    command = [convert, str(path), "-colorspace", "gray", "-depth", "8", "pgm:-"]
    data = (runner or _pgm_stdout)(command)
    if data is None:
        return None
    parsed = parse_pgm(data)
    if parsed is None:
        return None
    width, height, pixels = parsed
    return ink_components(width, height, pixels)


def screen_content_problem(
    path: Path,
    min_components: int,
    *,
    runner: Optional[Callable[[Sequence[str]], Optional[bytes]]] = None,
) -> Optional[str]:
    """Why ``path`` must not become a golden, or ``None`` when it looks rendered.

    This is the pixel backstop behind the view check: a capture of the glance or
    of an unpainted screen has far fewer ink blobs than a rendered menu. The
    guest measured 40 blobs for the glance and 74 for the sitemap menu within the
    device screen; a whole-window capture carries extra chrome blobs, so the
    threshold belongs to the scenario and is measured on review.
    """
    count = image_ink_components(path, runner=runner)
    if count is None:
        return None
    if count < min_components:
        return (
            f"{path} contains only {count} distinct ink blobs, fewer than the "
            f"{min_components} this scenario requires of a rendered view. A glance "
            "or an unpainted screen must never become a baseline; check that the "
            "screenshot step waits for the expected view, then re-capture."
        )
    return None
