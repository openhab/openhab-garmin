#!/usr/bin/env bash
# Runner container entrypoint.
#
# Fails fast and loudly when the Connect IQ SDK is missing or has the wrong
# version: a run against the wrong SDK produces results that cannot be compared
# with anyone else's, which is worse than no run at all.
#
# It also stages everything the compile needs: the SDK is copied out of its
# read-only mount into a writable path, and its device definitions are linked
# into the paths the toolchains search. The X server comes up first, because it
# is cheap and independent while staging copies ~250 MB.
set -euo pipefail

sdk_hint() {
    cat >&2 <<'MSG'
========================================================================
Connect IQ SDK is not available in the runner container.

The SDK is not redistributable, so it is never part of this repository or of
this image. Mount it on the host and point CIQ_SDK_HOST_PATH at it:

    export CIQ_SDK_HOST_PATH=/opt/garmin/connectiq-sdk-9.3.0
    docker compose up -d

Inside the container the mount is CIQ_SDK_MOUNT (/opt/ciq-sdk). It is mounted
read-only on purpose: monkeyc writes into the SDK, so entrypoint.sh copies it
to CIQ_SDK_WORKDIR (/tmp/ciq-sdk) and compiles there.

Device definitions are expected under the SDK (a "Devices"/"devices"
directory) or, when they live elsewhere, mounted read-only at
/opt/ciq-devices via CIQ_DEVICES_HOST_PATH. They are copied to
$HOME/.Garmin/ConnectIQ/Devices, which is where monkeyc looks for them.

Expected layout inside the mount:

    version.txt                 (or bin/version.txt)
    linux/bin/monkeyc           (or bin/monkeyc)
    linux/bin/monkeydo          (or bin/monkeydo)
    windows/bin/simulator.exe   (or bin/simulator.exe)
    devices/<device>/compiler.json  (or bin/devices, Devices, ...)

See tests/docs/sdk.md.
========================================================================
MSG
}

# One X server per container, started before anything is staged: staging copies
# the SDK (~250 MB) and validates it, and a container that reports healthy while
# no display is up yet makes a UI driver look like a display failure. Compose
# sets DISPLAY; start Xvfb only when the display is not already answering, so
# restarting the entrypoint is safe.
export DISPLAY="${DISPLAY:-:99}"
display_number="${DISPLAY##*:}"
if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    echo "using the X server already listening on $DISPLAY"
else
    # `docker restart` keeps the container filesystem, so the socket and lock of
    # the previous Xvfb survive with no server behind them, and Xvfb refuses to
    # start against a stale socket. Removing them is safe here: xdpyinfo just
    # confirmed that nothing is answering on this display.
    rm -f "/tmp/.X11-unix/X${display_number}" "/tmp/.X${display_number}-lock"
    Xvfb "$DISPLAY" -screen 0 1920x1080x24 -nolisten tcp &
    display_ready=false
    for _ in $(seq 1 40); do
        if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
            display_ready=true
            break
        fi
        sleep 0.25
    done
    if [ "$display_ready" != "true" ]; then
        echo "error: Xvfb did not start on $DISPLAY, so nothing can render" >&2
        echo "       check /tmp/.X11-unix/X${display_number} and /tmp/.X${display_number}-lock" >&2
        exit 78
    fi
fi

sdk_workdir="${CIQ_SDK_WORKDIR:-/tmp/ciq-sdk}"
sdk_source="${CIQ_SDK_MOUNT:-${CIQ_SDK_ROOT:-}}"

if [ -z "$sdk_source" ] || [ ! -d "$sdk_source" ]; then
    echo "error: the Connect IQ SDK mount is not a directory (got '${sdk_source:-}')" >&2
    sdk_hint
    exit 78
fi

if [ "$sdk_source" = "$sdk_workdir" ]; then
    # No mount was given and CIQ_SDK_ROOT already points at the copy, which is
    # the image default: a container that has started before already staged it.
    if [ -z "$(ls -A "$sdk_workdir" 2>/dev/null || true)" ]; then
        echo "error: no Connect IQ SDK mount was provided and $sdk_workdir is empty" >&2
        sdk_hint
        exit 78
    fi
    echo "reusing the staged Connect IQ SDK at $sdk_workdir"
else
    # monkeyc rewrites $SDK/bin/default.jungle, so a read-only SDK at the path
    # the compiler uses cannot work. The operator's mount is never written: it
    # is copied to a writable path and CIQ_SDK_ROOT points at the copy. A
    # restarted container re-runs this, so a changed SDK is picked up.
    echo "staging the Connect IQ SDK: $sdk_source -> $sdk_workdir"
    mkdir -p "$(dirname "$sdk_workdir")"
    rm -rf "$sdk_workdir"
    cp -a "$sdk_source" "$sdk_workdir"
fi
export CIQ_SDK_ROOT="$sdk_workdir"

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

# monkeyc finds device definitions through the user-level Connect IQ directory,
# not through the SDK path. Link them before anything compiles: an unknown
# device otherwise only shows up as a failed build much later.
if ! python -m e2e link-devices --quiet; then
    cat >&2 <<'MSG'
error: no Connect IQ device definitions were found.

monkeyc needs them to resolve -d <device>; without them every build fails with
"cannot find device". Either use an SDK that ships a Devices/ directory, or
mount the definitions read-only and name them in tests/compose/.env:

    CIQ_DEVICES_HOST_PATH=/opt/garmin/ConnectIQ/Devices

They are staged in two places, because two toolchains look for them:
$HOME/.Garmin/ConnectIQ/Devices for the Linux monkeyc/monkeydo, and the Wine
prefix's drive_c/users/<user>/AppData/Roaming/Garmin/ConnectIQ/Devices for the
Windows simulator.
See tests/docs/sdk.md.
MSG
    exit 78
fi

# The simulator opens the font pack before every draw and crashes with
# "Invalid Font Specified" when it is absent, so a missing pack is a hard
# failure like a missing SDK. The fonts are licensed Garmin assets: they are
# mounted read-only and staged here, never committed and never baked in.
if ! python -m e2e link-fonts --quiet; then
    exit 78
fi

if ! python -m e2e sdk-check --quiet; then
    sdk_hint
    exit 78
fi

exec "$@"
