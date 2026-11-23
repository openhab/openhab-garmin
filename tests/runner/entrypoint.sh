#!/usr/bin/env bash
# Runner container entrypoint.
#
# Fails fast and loudly when the Connect IQ SDK is missing or has the wrong
# version: a run against the wrong SDK produces results that cannot be compared
# with anyone else's, which is worse than no run at all.
set -euo pipefail

sdk_hint() {
    cat >&2 <<'MSG'
========================================================================
Connect IQ SDK is not available in the runner container.

The SDK is not redistributable, so it is never part of this repository or of
this image. Mount it and point CIQ_SDK_ROOT at it on the host:

    export CIQ_SDK_ROOT=/opt/garmin/connectiq-sdk-9.3.0
    docker compose up -d

Expected layout inside the mount:

    version.txt                 (or bin/version.txt)
    linux/bin/monkeyc           (or bin/monkeyc)
    linux/bin/monkeydo          (or bin/monkeydo)
    windows/bin/simulator.exe   (or bin/simulator.exe)

See tests/docs/sdk.md.
========================================================================
MSG
}

if [ -z "${CIQ_SDK_ROOT:-}" ] || [ ! -d "${CIQ_SDK_ROOT}" ]; then
    echo "error: CIQ_SDK_ROOT is not set to a directory (got '${CIQ_SDK_ROOT:-}')" >&2
    sdk_hint
    exit 78
fi

# The harness itself lives on the bind-mounted checkout. If the mount is
# missing, say so instead of failing later with an import error.
if ! python -c "import e2e" 2>/dev/null; then
    cat >&2 <<'MSG'
error: the harness is not importable. Mount the repository at /work:

    volumes:
      - ${E2E_REPO_ROOT:-../..}:/work:ro

and keep PYTHONPATH=/work/tests/runner:/work/tests.
MSG
    exit 78
fi

if ! python -m e2e sdk-check --quiet; then
    sdk_hint
    exit 78
fi

# One X server per container. Compose sets DISPLAY; start Xvfb only when the
# display is not already answering, so restarting the entrypoint is safe.
export DISPLAY="${DISPLAY:-:99}"
if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    Xvfb "$DISPLAY" -screen 0 1920x1080x24 -nolisten tcp &
    for _ in $(seq 1 20); do
        if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
            break
        fi
        sleep 0.25
    done
fi

exec "$@"
