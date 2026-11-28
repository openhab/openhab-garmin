"""Simulator drivers."""

from __future__ import annotations

from typing import Optional

from ..sdk import SdkLayout
from .base import Button, CONNECTION_TRACE_MODES, DriverError, RUNNABLE_LANGUAGES, SimulatorDriver
from .native import NativeSimulatorDriver
from .null import NullDriver
from .wine import WineSimulatorDriver

#: Drivers that can be selected with ``--driver``. ``wine`` is still the default
#: for ``run``: it is the driver the corpus was recorded on, and ``native`` must
#: pass the same corpus on the VM before it takes over (see
#: ``tests/docs/simulator-driver.md``). ``native`` is the target driver and the
#: only one that can change the connection mode while the app runs.
DRIVER_NAMES = ("null", "wine", "native")


def create_driver(
    name: str,
    *,
    layout: Optional[SdkLayout] = None,
    wineprefix=None,  # type: ignore[no-untyped-def]
    display: Optional[str] = None,
    artifact_dir=None,  # type: ignore[no-untyped-def]
    log=None,  # type: ignore[no-untyped-def]
) -> SimulatorDriver:
    """Instantiate a driver by name.

    ``null`` is the dry-run driver. ``wine`` and ``native`` need a validated SDK
    layout and a real PRG before they can launch anything; ``native`` also needs
    the Linux SDK's ``linux/bin/simulator``.
    """
    if name == "null":
        return NullDriver(log=log)
    if name == "wine":
        if layout is None:
            raise DriverError("the 'wine' driver needs a validated Connect IQ SDK layout")
        return WineSimulatorDriver(
            layout,
            wineprefix=wineprefix,
            display=display,
            artifact_dir=artifact_dir,
            log=log,
        )
    if name == "native":
        if layout is None:
            raise DriverError("the 'native' driver needs a validated Connect IQ SDK layout")
        return NativeSimulatorDriver(
            layout,
            display=display,
            artifact_dir=artifact_dir,
            log=log,
        )
    raise DriverError(f"unknown driver {name!r}; known drivers: {', '.join(DRIVER_NAMES)}")


__all__ = [
    "Button",
    "CONNECTION_TRACE_MODES",
    "DRIVER_NAMES",
    "DriverError",
    "NativeSimulatorDriver",
    "NullDriver",
    "RUNNABLE_LANGUAGES",
    "SimulatorDriver",
    "WineSimulatorDriver",
    "create_driver",
]
