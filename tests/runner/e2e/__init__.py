"""E2E harness runner package.

Layout (all paths are relative to the repository root)::

    tests/runner/e2e/     this package
    tests/config/         version pins, the device matrix and the network definition
    tests/fixtures/       openHAB conf trees and app settings
    tests/scenarios/      scenario YAML, grouped by suite
    tests/shim/           the proxy/fault injector (also imported from here)
    tests/compose/        the Docker Compose stack
    tests/docs/           harness documentation

The package is imported both from a checkout (`python -m e2e` in
`tests/runner`) and inside the runner image, so it must not depend on the
current working directory.
"""

from .paths import ensure_import_paths

ensure_import_paths()

__all__ = ["ensure_import_paths"]
