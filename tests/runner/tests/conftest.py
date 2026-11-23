"""Shared test setup.

These tests run on a workstation without Docker and without the Connect IQ
SDK. They only cover pure logic: scenario loading, the shim fault grammar,
static Compose validation, settings fixtures, SDK layout probing and a dry-run
of the engine.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[2]
RUNNER_ROOT = TESTS_ROOT / "runner"

for path in (TESTS_ROOT, RUNNER_ROOT):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)
