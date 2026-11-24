#!/bin/sh
# Stage the fixture tree into an openHAB conf volume.
#
# Run as the one-shot openhab-<profile>-init service before the openHAB
# container starts (see compose.yaml). Why this exists:
#
#   The openHAB image's entrypoint ends with `chown -R openhab:openhab /openhab`
#   under `set -e`. A read-only bind mount anywhere underneath /openhab aborts
#   the container on first start:
#
#     chown: changing ownership of '/openhab/conf/items/garmin_e2e.items':
#     Read-only file system
#
#   The fixtures are the source of truth and must stay read-only, and the
#   operator's checkout must never be written to, so they are copied into the
#   writable conf volume instead of being mounted into it.
#
# Idempotency:
#
#   - The dist skeleton is seeded only while the volume is empty, which mirrors
#     the image's own `initialize_volume`: openHAB skips its seeding because the
#     volume is no longer empty, so the skeleton has to come from here.
#   - The fixture directories mirror the checkout exactly and are replaced on
#     every run, so a second `up` cannot duplicate files and a stale fixture is
#     removed. `docker compose down -v` followed by `up` is a full reset.
#
# No chown here: the openHAB entrypoint owns the volume ownership (uid/gid
# 9001) and can chown the whole tree once nothing underneath it is read-only.
set -eu

conf="${OPENHAB_CONF:-/openhab/conf}"
dist="${OPENHAB_HOME:-/openhab}/dist/conf"
fixtures="${FIXTURES_DIR:-/fixtures}"

if [ ! -d "$fixtures" ]; then
    echo "error: fixture tree $fixtures is missing; mount fixtures/openhab/conf at $fixtures" >&2
    exit 1
fi
if [ ! -d "$dist" ]; then
    echo "error: openHAB dist config $dist is missing; seed-openhab-conf.sh must run in the openHAB image" >&2
    exit 1
fi

mkdir -p "$conf"
if [ -z "$(ls -A "$conf" 2>/dev/null || true)" ]; then
    echo "seeding empty conf volume $conf from $dist"
    cp -a "$dist/." "$conf/"
fi

for name in items sitemaps; do
    rm -rf "${conf:?}/$name"
    mkdir -p "$conf/$name"
    if [ -d "$fixtures/$name" ]; then
        cp -a "$fixtures/$name/." "$conf/$name/"
    fi
done

echo "staged fixtures from $fixtures into $conf"
