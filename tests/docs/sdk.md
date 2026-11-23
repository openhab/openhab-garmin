# Connect IQ SDK: layout, pinning and licence handling

## The SDK is never in this repository or in an image

Garmin's Connect IQ SDK agreement does not permit redistribution. Therefore:

- no SDK file is committed to this repository;
- no image built from this repository contains SDK files;
- the operator downloads the SDK, or mounts an archive they already have.

The harness reads the SDK location from `CIQ_SDK_HOST_PATH` on the host, mounts it
read-only at `/opt/ciq-sdk`, and exports that mount point as `CIQ_SDK_ROOT` inside
the container. `e2e.sdk` also honours `CIQ_SDK_PATH` as an alias, because the
acceptance criteria in issue #365 use that name.

If the SDK is missing, unreadable, or is the wrong version, the runner container
exits with code `78` and a message that names the variable to set and the expected
layout. It does not fall back to a "best effort" build: results from an unknown
SDK cannot be compared with anyone else's.

## Accepted layout

```text
<CIQ_SDK_HOST_PATH>/
  version.txt                  or bin/version.txt
  linux/bin/monkeyc            or bin/monkeyc
  linux/bin/monkeydo           or bin/monkeydo          (optional)
  windows/bin/simulator.exe    or bin/simulator.exe     (optional)
```

Both spellings are accepted because the Linux SDK archive and the Windows SDK
archive use different directory layouts, and a contributor may have either.

- `monkeyc` is required: the runner compiles the PRG from the checkout.
- `monkeydo` is required to launch the app in the simulator.
- `simulator.exe` is required for `ui`-tier scenarios only. Protocol-tier scenarios
  can build and assert without it (`e2e sdk-check` reports it as absent).

## Version pinning

`tests/config/sdk.version` pins the SDK version (currently `9.3.0`). A mismatch is a
hard error that names both versions.

Bump it deliberately and in the same change as any app behaviour that depends on the
new SDK. Record why in the commit message: the harness exists to make regressions
visible, and a floating SDK would make "it passed last week" meaningless.

## Checking your setup

```bash
cd tests/runner
export CIQ_SDK_ROOT=/opt/garmin/connectiq-sdk-9.3.0   # or CIQ_SDK_PATH
python -m e2e sdk-check
```

Expected output names the root, the version (and the pin), `monkeyc`, `monkeydo` and
the simulator, or explains exactly which of those is missing.

Inside the stack, the same check runs from `runner/entrypoint.sh` before anything
else.

## Why the module also knows about the simulator

The simulator can only be driven from Windows or Wine. The SDK layout is the only
place the harness learns where `simulator.exe` lives, so `e2e.sdk` validates both
tools. See [simulator-driver.md](simulator-driver.md) for the driver itself.

## What an operator must not do

- Do not commit SDK files, `.prg` files or SDK licences.
- Do not add an SDK download step to a Dockerfile: the SDK must arrive at run time,
  from the operator's own licence holder.
- Do not pin a "latest" SDK tag: the version pin is part of the test definition.
