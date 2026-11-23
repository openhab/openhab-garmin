"""openHAB Garmin E2E shim package.

The shim sits between the Garmin app and openHAB. It records every request it
sees and can replace the upstream response with a scripted fault.

Two modules matter:

- :mod:`shim.faults` -- the fault-script grammar. Pure functions, no I/O, and
  shared with the runner so both sides validate scripts identically.
- :mod:`shim.server` -- the HTTP server. Standard library only, so the image
  needs no wheels and cannot drift from what was reviewed.
"""
