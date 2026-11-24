"""Simulator drivers."""

from __future__ import annotations

from typing import Optional

from ..sdk import SdkLayout
from .base import Button, DriverError, RUNNABLE_LANGUAGES, SimulatorDriver
from .null import NullDriver
from .wine import WineSimulatorDriver

DRIVER_NAMES = ("null", "wine")


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

    ``null`` is the dry-run driver. ``wine`` needs a validated SDK layout and a
    real PRG before it can launch anything.
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
    raise DriverError(f"unknown driver {name!r}; known drivers: {', '.join(DRIVER_NAMES)}")


__all__ = [
    "Button",
    "DRIVER_NAMES",
    "DriverError",
    "NullDriver",
    "RUNNABLE_LANGUAGES",
    "SimulatorDriver",
    "WineSimulatorDriver",
    "create_driver",
]
