# Connect IQ SDK: layout, pinning and licence handling

## The SDK is never in this repository or in an image

Garmin's Connect IQ SDK agreement does not permit redistribution. Therefore:

- no SDK file is committed to this repository;
- no image built from this repository contains SDK files;
- the operator downloads the SDK, or mounts an archive they already have.

The harness reads the SDK location from `CIQ_SDK_HOST_PATH` on the host and mounts
it read-only at `/opt/ciq-sdk` (`CIQ_SDK_MOUNT` inside the container). It cannot
use the mount directly: `monkeyc` rewrites `$SDK/bin/default.jungle`, and the
operator's SDK must not be mutated. At container start `entrypoint.sh` therefore
copies the mount to `CIQ_SDK_WORKDIR` (`/tmp/ciq-sdk`) and exports
`CIQ_SDK_ROOT` to the copy. `e2e.sdk` also honours `CIQ_SDK_PATH` as an alias,
because the acceptance criteria in issue #365 use that name.

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
  devices/<device>/compiler.json
                           or  bin/devices, Devices, linux/bin/Devices, ...
```

Both spellings are accepted because the Linux SDK archive and the Windows SDK
archive use different directory layouts, and a contributor may have either.

- `monkeyc` is required: the runner compiles the PRG from the checkout. It is a
  shell wrapper around `monkeybrains.jar`, so the runner image installs a
  headless JRE; a host that runs `monkeyc` directly needs Java 11 or newer.
- `monkeydo` is required to launch the app in the simulator.
- `simulator.exe` is required for `ui`-tier scenarios only. Protocol-tier scenarios
  can build and assert without it (`e2e sdk-check` reports it as absent).
- the device definitions for the device under test are required to compile at
  all; see [Device definitions](#device-definitions) and
  [Device-scoped jungle](#device-scoped-jungle).

## Writable copy at container start

The mount is read-only by design, but the compiler is not: `monkeyc` writes
`bin/default.jungle` into the SDK, and a read-only SDK fails every build with

```text
ERROR: fenix7: Error occurred while processing application jungle file:
/opt/ciq-sdk/linux/bin/default.jungle (Read-only file system)
```

`entrypoint.sh` copies `CIQ_SDK_MOUNT` to `CIQ_SDK_WORKDIR` (`/tmp/ciq-sdk`) and
exports `CIQ_SDK_ROOT` to the copy before anything compiles. The copy is
recreated on every container start, so a changed SDK is picked up by restarting
the container. This is the same thing the repository's CI does (it copies the SDK
into `$HOME/.Garmin/ConnectIQ` and git-ignores `Sdks/*/bin/default.jungle`).

`CIQ_SDK_ROOT` is also declared in the **image environment**, pointing at the
copy. `docker exec` starts a new process from the image config and never sees
the entrypoint's `export`, so without the image default every command driven
through `exec` - exactly how a driver would use the runner - fails with
"Connect IQ SDK not configured" even though the copy and the linked device
definitions are there. `CIQ_SDK_ROOT` and `CIQ_SDK_WORKDIR` must stay equal; a
test asserts it.

Two consequences worth knowing:

- the copy occupies the size of the SDK in the container's writable layer for as
  long as the container exists (`preflight` requires 5 GiB free disk);
- do **not** solve a read-only error by mounting the SDK read-write. The mount is
  the operator's copy of a licence-restricted download; the compiler rewriting it
  would make runs non-repeatable.

## Device definitions

`monkeyc -d <device>` resolves the device from the user-level Connect IQ
directory, not from the SDK path. Passing `-d fenix7` against a perfectly valid
mount therefore fails until the definitions are visible at
`$HOME/.Garmin/ConnectIQ/Devices`:

```text
Cannot find device: fenix7
```

`depends_on`-style folklore is not needed: `entrypoint.sh` runs
`python -m e2e link-devices`, which finds the definitions and stages them in
**two** places, because two toolchains read them. It looks, in order, at:

1. `CIQ_DEVICES_ROOT`, when set (it must look like a definitions root);
1. `/opt/ciq-devices`, the mount fed by `CIQ_DEVICES_HOST_PATH`;
1. `<sdk>/Devices`, `<sdk>/devices`, `<sdk>/bin/...`, `<sdk>/linux/...`,
   `<sdk>/linux/bin/...`, `<sdk>/windows/bin/...`;
1. a bounded walk (three levels) for a `devices` directory that contains at
   least one device subdirectory with `compiler.json`.

If none is found the container exits with code `78` and a message that lists
every path it searched — a missing definition is a configuration error, not a
build failure to decode later. The repository's CI commits a reduced set at
`.github/ConnectIQ/Devices`; that set is usable here because the harness
compiles a jungle scoped to the device under test, see
[Device-scoped jungle](#device-scoped-jungle).

Both staged locations are required, and each one alone breaks a different
consumer:

| Path | Consumer | If it is missing |
| --- | --- | --- |
| `$HOME/.Garmin/ConnectIQ/Devices` | Linux `monkeyc` / `monkeydo` | every build fails with `Cannot find device: <device>` |
| `<WINEPREFIX>/drive_c/users/<user>/AppData/Roaming/Garmin/ConnectIQ/Devices` | `simulator.exe` under Wine | the simulator opens an `Error` window with no device, so the whole `ui` tier renders nothing |

The Linux path is a copy, because the compiler may write caches there. The Wine
path is a symlink to that copy, so there is exactly one set of files; where
symlinks are unavailable it falls back to a copy. `WINEPREFIX` defaults to
`$HOME/.wine`, and the per-user directory follows the UNIX user name the same
way Wine derives it.

`python -m e2e link-devices` is also useful outside the container; `--target`
overrides the Linux destination, and the Wine link follows it. On a restart the
container re-runs the whole staging step, so both paths are re-asserted.

## Device fonts

The simulator resolves every face it draws from
`$HOME/.Garmin/ConnectIQ/Fonts`, and the Wine prefix's `ConnectIQ/Fonts` for
`simulator.exe`. The SDK archive does **not** ship them: `find <sdk> \( -iname
'*.fnt' -o -iname '*.ttf' \)` finds only the assets of `samples/Analog`. With the
directory empty the app crashes on its first draw with
`Error: Invalid Font Specified` and the screen stays on the watch face — on the
native simulator and under Wine alike.

They come from a working Connect IQ installation: `%APPDATA%\Garmin\ConnectIQ\Fonts`
on Windows, which holds the `.cft` compiled faces (plus `.ttf` sources and `.md5`
sidecars) that `Devices/<device>/simulator.json` names by filename.

Staging mirrors the device definitions. The source is, in order:

1. `CIQ_FONTS_ROOT`, when set (it must contain `.cft` or `.ttf` files);
1. `/opt/ciq-fonts`, the mount fed by `CIQ_FONTS_HOST_PATH`;
1. `$HOME/.Garmin/ConnectIQ/Fonts` itself, when an operator already populated it.

`entrypoint.sh` runs `python -m e2e link-fonts`, which links the pack into both
locations. Both are required, exactly as for the devices:

| Path | Consumer | If it is missing |
| --- | --- | --- |
| `$HOME/.Garmin/ConnectIQ/Fonts` | native `linux/bin/simulator` | `Invalid Font Specified` on the first draw |
| `<WINEPREFIX>/drive_c/users/<user>/AppData/Roaming/Garmin/ConnectIQ/Fonts` | `simulator.exe` under Wine | the same crash |

A missing, empty or face-less directory is a hard failure at container start
(`exit 78`), and `run --update-goldens` refuses without it, naming the directory:
a capture from a crashed render must never become a baseline.

`python -m e2e link-fonts` stages the pack from `--mount` (or the sources above)
into `--target` and the Wine prefix.

### Licensing

The fonts are licensed Garmin assets, exactly like the SDK: mount or copy them
from a working installation, never commit them, never bake them into an image,
and never fetch them from a third party.

## Device-scoped jungle

`monkey.jungle` declares configuration for every supported device, and
`monkeyc` validates every device qualifier it finds. Compiling the full file
therefore needs an SDK whose device database contains all of them. A reduced
SDK — including the set committed at `.github/ConnectIQ/Devices` — fails with

```text
ERROR: /work/monkey.jungle:449: 'edge550' is not a valid device / family qualifier.
ERROR: /work/monkey.jungle:450: 'edge550' is not a valid device / family qualifier.
```

`e2e build` and `e2e run` therefore generate a jungle for the device under test
before compiling, with the same logic as `.github/scripts/monkeyc-build`:

- keep the `base` section and the selected device's section;
- follow `$(device.` cross references transitively — for example
  `venu3.excludeAnnotations = $(approachs50.excludeAnnotations)` also keeps
  `approachs50`;
- drop every other device section, while comments, blank lines and non-path
  settings (`project.typecheck`) are copied verbatim;
- rewrite every path that refers to the checkout to an absolute path rooted at
  the checkout: `project.manifest`, path-valued `project.*` settings,
  `resourcePath` and `sourcePath` entries, and the variables they reference.
  `monkeyc` resolves a relative path in a jungle file against _that file's_
  directory, and the checkout may be read-only, so the generated file cannot
  live in the project root;
- write the result to `<artifacts>/build/<device>.jungle`, outside the possibly
  read-only checkout.

The generated jungle is portable: it does not depend on the directory it was
written to, and it never needs write access to the checkout.

The harness therefore no longer requires a complete SDK to compile: it needs the
device definitions for the device under test, not for the whole matrix. The
build output records which jungle was used and why.

A complete SDK can still compile the full jungle deliberately. `--jungle <path>`
is honoured by `e2e build` and `e2e run` and uses the file verbatim, so the SDK
must define every device the file names:

```bash
cd tests/runner
python -m e2e build --device fenix7 --jungle ../../monkey.jungle
```

If `monkeyc` rejects a qualifier anyway, the harness replaces the raw compiler
dump with a message that names the missing device, says that the SDK's device
set is narrower than the jungle, and points at `--device` and this document.

## Developer key

`monkeyc` refuses to compile without `-y <key>`:

```text
The private key was not specified. Use command line option: -y.
```

`e2e build` and `e2e run` therefore generate a throwaway RSA key per invocation:
`openssl genrsa 4096` followed by `openssl pkcs8 -topk8 -outform DER -nocrypt`,
written to `<artifacts>/keys/` with mode `0600` and deleted when the command
ends. It is ephemeral by design: it signs a build for the simulator, it is not
the Connect IQ store key, it is never committed and it is never reused.
`--developer-key <file>` remains the override when a stable key is needed.

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
export CIQ_FONTS_HOST_PATH=/home/<user>/.Garmin/ConnectIQ/Fonts   # or link-fonts
python -m e2e sdk-check
python -m e2e link-devices
python -m e2e link-fonts
```

Expected output names the root, the version (and the pin), `monkeyc`, `monkeydo`,
the simulator, the device definitions and the font pack, or explains exactly
which of those is missing. Inside the stack the same commands run from
`entrypoint.sh` after the SDK has been copied to its writable path; a missing
font pack stops the container rather than producing a run that renders nothing.

## Why the module also knows about the simulator

The simulator can only be driven from Windows or Wine. The SDK layout is the only
place the harness learns where `simulator.exe` lives, so `e2e.sdk` validates both
tools. See [simulator-driver.md](simulator-driver.md) for the driver itself.

## What an operator must not do

- Do not commit SDK files, `.prg` files or SDK licences.
- Do not commit the device font pack either: it is licensed Garmin content.
  Mount it (`CIQ_FONTS_HOST_PATH`) or copy it into the environment.
- Do not add an SDK download step to a Dockerfile: the SDK must arrive at run time,
  from the operator's own licence holder.
- Do not mount the SDK read-write to work around the compiler writing into it;
  the entrypoint copy exists for that, and it keeps the mounted download intact.
- Do not pin a "latest" SDK tag: the version pin is part of the test definition.
