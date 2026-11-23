"""Import-path helpers.

The runner and the shim live in sibling directories and are deployed as two
images, but they share one Python module (``shim.faults``). Rather than relying
on ``PYTHONPATH`` being set correctly in every environment -- checkout, image,
IDE, pytest -- the package makes both roots importable itself.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Tuple


def tests_root() -> Path:
    """Absolute path of the ``tests/`` directory."""
    # .../tests/runner/e2e/paths.py -> e2e -> runner -> tests
    return Path(__file__).resolve().parents[2]


def runner_root() -> Path:
    """Absolute path of the ``tests/runner/`` directory."""
    return Path(__file__).resolve().parents[1]


def repo_root() -> Path:
    """Absolute path of the repository checkout."""
    return tests_root().parent


def import_roots() -> Tuple[Path, Path]:
    """Directories that must be on ``sys.path`` for the harness to import."""
    return tests_root(), runner_root()


def ensure_import_paths() -> None:
    """Insert the harness roots into ``sys.path`` if they are missing.

    The roots are inserted at the front so a stale installed copy of a package
    with the same name cannot shadow the checkout.
    """
    for path in reversed(import_roots()):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)
