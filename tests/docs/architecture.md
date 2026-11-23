# Harness architecture

## Components

```mermaid
flowchart LR
    subgraph stack["Docker Compose (tests/compose)"]
        runner["runner<br/>Ubuntu 24.04, Wine, Xvfb,<br/>xdotool, ImageMagick, Python 3"]
        shim["shim<br/>proxy + fault injector<br/>records every request"]
        stable["openhab-stable<br/>profile: stable (default)"]
        dev["openhab-dev<br/>profile: dev (opt-in)"]
        fx[("fixtures<br/>items + sitemaps (ro)")]
        arts[("e2e-artifacts")]
    end
    sdk[("Connect IQ SDK<br/>mounted ro, never baked")]
    sdk --> runner
    runner -->|"monkeyc builds PRG"| runner
    runner -->|"app HTTP requests"| shim
    shim -->|"proxy"| stable
    shim -.->|"proxy (dev override)"| dev
    fx --> stable
    fx -.-> dev
    runner --> arts
```

| Component | Responsibility | Never does |
| --- | --- | --- |
| `runner` | build the PRG, drive one simulator, run steps, assert, report | contain or download the SDK |
| `shim` | record requests, inject scripted faults | know about the app's internals |
| `openhab-stable` / `openhab-dev` | serve the sitemap, apply item state | talk to the app directly |
| fixtures | define item structure and the sitemap | hold item _state_ |

## Request flow

1. The app polls `GET {shim}/rest/sitemaps/garmin_e2e`.
1. The shim matches the request against the installed fault script.
   - No match: proxy to openHAB and return its response.
   - Match: synthesise the scripted status/body/delay, or reset the connection.
1. The shim records method, target, body, outcome and duration.
1. The runner asserts on that trace, on the app's `E2E:` console events, and
   optionally on a screenshot.

Commands follow the same path: `POST {shim}/rest/items/{name}`.

Setup traffic is deliberately **not** routed through the shim. The runner seeds
item state over REST directly against openHAB, so the trace only contains what the
app did. That is what makes "exactly one command was sent" a meaningful assertion.

## Deployment profiles and exposure

The same documents run in two networking modes. Ports are never repeated in a
file: [`config/network.yaml`](../config/network.yaml) defines the loopback host
and ports, renders the `network` block in
[`compose/.env.example`](../compose/.env.example) and seeds the app settings
fixture URL (`${E2E_SHIM_URL}`, resolved from the runner's environment).

| Profile | Overlay files | Peer addressing | Host listeners |
| --- | --- | --- | --- |
| bridge, stable (default) | `compose.yaml` | Compose service aliases, container port 8080 | `127.0.0.1:<port>` only |
| bridge, dev | `compose.yaml`, `compose.dev.yaml` | `openhab-dev:8080`, `shim:8080` | `127.0.0.1:<port>` only |
| host, stable | `compose.yaml`, `compose.host.yaml` | `http://127.0.0.1:<port>` | `127.0.0.1` only |
| host, dev | the above plus `compose.dev.yaml`, `compose.host.dev.yaml` | `http://127.0.0.1:<dev port>` | `127.0.0.1` only |

The bridge profile is the portable one: it works on macOS and Windows Docker
hosts, and publishing on loopback keeps a developer machine's LAN out of the
stack. The host overlays exist for the dedicated Linux VM: they set
`network_mode: host`, drop the inherited publications with Compose's `!reset`
tag, bind the shim to `${E2E_LOOPBACK_HOST}`, and give openHAB
`-Dorg.ops4j.pax.web.listening.address=${E2E_LOOPBACK_HOST}` because its default
is `0.0.0.0`.

`python -m e2e validate-compose` validates all four files and fails when a
published port lacks a loopback host, when the host overlay addresses a peer by
hostname, or when the environment block and the fixtures disagree with
`network.yaml`. `tests/runner/tests/test_network.py` also rejects any
site-specific hostname or certificate/key material under `tests/`.

External access is **not** part of the stack. If the host must be reachable from
elsewhere, the operator terminates TLS in a reverse proxy in front of the
loopback port. No certificate, key or CA material is referenced here or stored
in the environment.

## Assertion layers

| Layer | Signal | Stability | Used by |
| --- | --- | --- | --- |
| openHAB REST state | item state after a step | high | scenarios that verify the command actually applied |
| shim request trace | method, target, body, status, reset, timing | high | `protocol` tier, all command assertions |
| app trace events (`E2E:`) | view, connection, sitemap result, command result, error class, toast, error view | high, debug builds only | `protocol` tier |
| golden screenshots | pixel difference against a reviewed baseline | medium: SDK-version sensitive | `ui` tier |

The ordering is deliberate: a failing trace assertion tells you _what_ broke, and
the screenshot tells you _how it looked_. Screenshots alone are advisory.

## Failure model

- **Assertion failure** – the step fails, the scenario stops, artifacts are kept.
- **Infrastructure failure** (openHAB/shim unreachable, driver missing) – reported
  as a failed step with the cause, never as a pass.
- **Not evaluable** (dry run without events/screenshots, `--skip-events`) – the
  step is recorded as `skipped` and counted in the report. A skipped step is never
  a success.
- **Scenario budget** (`timeout_s`) – checked before each step, so a hung simulator
  or a scripted multi-second delay cannot stall a suite indefinitely.

## Portability boundary

This directory is the complete test unit. Everything environment-specific belongs
to the infrastructure repository:

- where the stack runs (VM, CI runner, workstation);
- how Docker is provided;
- how the stack is reached from outside the host and where TLS is terminated;
- where the SDK archive comes from;
- secrets (API tokens for openHAB admin operations, if a scenario ever needs them).

Nothing here reads a host path other than `CIQ_SDK_HOST_PATH` (mount) and
`E2E_REPO_ROOT` (checkout mount). Nothing here contains a site-specific
hostname: the dedicated host reaches the services as `127.0.0.1` and the app
settings fixtures resolve their endpoint from the stack environment.
