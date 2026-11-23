"""Connect IQ simulator under Wine -- feasibility spike (#370).

This package is deliberately **not** part of the E2E harness. It answers one
question: can the Windows Connect IQ simulator be driven reliably enough under
Wine/Xvfb to justify implementing ``WineSimulatorDriver``?

Layout::

    tests/spike/model.py      check/result model shared by every layer
    tests/spike/prereqs.py    host prerequisite checks (Docker, SDK)
    tests/spike/criteria.py   #370 pass criteria and the verdict
    tests/spike/report.py     verdict.json + verdict.md writers
    tests/spike/procedure.py  host orchestrator (build image, run probe)
    tests/spike/probe.py      in-container procedure (Wine, Xvfb, xdotool)
    tests/spike/cli.py        ``python -m spike`` command line

The host side and the in-container probe share nothing but the JSON document in
:mod:`spike.model`; that split keeps the verdict computation testable without
Docker, Wine or the SDK.

Nothing in this package runs in CI. The harness job only imports the pure
modules from ``tests/runner/tests/test_spike.py``.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
