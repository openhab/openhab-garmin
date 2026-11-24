# Stack health and run diagnostics

An E2E run has two possible verdicts that look identical in a naive report:
the app misbehaved, or the environment died underneath it. This document
explains how to tell them apart, what the harness records for every run, and
what to do when a run fails.

The classification is deliberate and narrow. It reports environment changes and
nothing else: it never turns an environment problem into a scenario failure, and
it never asserts on application behaviour, so it cannot hide an app defect.

## Why `docker ps` is not enough

`docker ps` and the container health status describe the stack at the instant
they are read. They are blind to the failure mode that matters most here:

- **The health status is the healthcheck's last result.** A container that is
  killed and restarted between two healthcheck intervals passes the next check
  and is reported `healthy` throughout. `docker ps` shows `Up 3 minutes`, not
  "was killed twice during the run".
- **`State.OOMKilled` is set by Docker, but the kernel performs the kill.** A
  cgroup v2 memory kill happens inside the kernel; the daemon never sees an exit
  it can attribute to memory pressure, so the flag stays `false`.
- **`docker ps` shows one state, not a history.** Restarting, exited, paused and
  dead all appear as a status column, with no indication of what changed since
  the run started.

This is not hypothetical. On `vm-ai-agents` two Freqtrade containers had been
OOM-killed roughly hourly for days — 520 MB anonymous RSS against a 512 MB cgroup
limit, no swap, 36 kills — while `docker ps` reported both **healthy**. Only the
restart count revealed it. A run against that stack looks exactly like a run
against a healthy one until someone reads the counters.

## Signals that do answer the question

| Signal | Where it lives | What it proves | What it does not prove |
| --- | --- | --- | --- |
| Restart count | `docker inspect .RestartCount` | the container was restarted since it was created | when it restarted, or why |
| Container state | `docker inspect .State.Status`, `.State.Running` | the container is (still) up | that it stayed up |
| Health status | `docker inspect .State.Health.Status` | the healthcheck last succeeded | that a kill did not happen between checks |
| Working set | `docker stats`, cgroup `memory.current` | memory the cgroup is accounting for now | the peak, or the cause of a kill |
| Peak working set | cgroup `memory.peak` (cgroup v2) | the highest working set since the container started | when the peak happened |
| OOM kills | cgroup `memory.events` `oom_kill` (v2), `memory.oom_control` (v1) | the kernel killed a process in this cgroup | which process, or why the allocation was refused |
| Kernel OOM lines | `dmesg` / `journalctl -k` | the kernel's own account of the kill | anything about containers that did not log one |

### Restart count, state and health

```bash
docker compose -p openhab-garmin-e2e ps -q |
  xargs docker inspect \
    --format '{{.Name}} restarts={{.RestartCount}} state={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} oomkilled={{.State.OOMKilled}}'
```

`RestartCount` is cumulative for the container's lifetime, so the number alone
is ambiguous: it includes restarts from earlier runs. Only the **delta between
the run's before and after snapshots** is attributable to the run window, and
that delta is what the harness classifies. Two consequences are worth knowing:

- A container that was already restarted before the run is reported as a note,
  not as an invalid run. Otherwise a single crash would invalidate every later
  run until someone recreated the container.
- `docker compose up` recreates containers and resets the counter. A counter
  that goes _down_ between two snapshots means the container was recreated
  during the window, which the harness reports as its own cause.

A missing `Health` object means the service has no healthcheck (the `runner`
service has none), so its health status proves nothing in either direction.

### Working set, not RSS

RSS is not the number the memory cgroup accounts for. The cgroup counts the
process's anonymous memory **plus** page cache and kernel memory; RSS leaves the
page cache out. A container whose page cache fills the limit can be reclaimed
harmlessly, while a container whose anonymous RSS approaches the limit is the one
that gets killed — and only the cgroup total shows both. The metric that matches
the limit is the working set:

- `container_memory_working_set_bytes` in cAdvisor;
- the `MEM USAGE` column of `docker stats --no-stream`, which is the cgroup
  usage minus inactive file pages;
- `memory.current` inside the container (cgroup v2).

```bash
docker stats --no-stream --format '{{json .}}' <container>
docker exec <container> cat /sys/fs/cgroup/memory.current
docker exec <container> cat /sys/fs/cgroup/memory.peak
```

**The limit column of `docker stats` is not a limit.** When no per-container
limit is set, Docker prints the host's total memory there. The Compose files in
this repository set no `mem_limit`, so `HostConfig.Memory` is `0` for every
service and the effective cgroup limit is the host's. The health block therefore
reports the limit as `unlimited` and has no headroom to report; the host total is
never presented as a limit. A per-service limit belongs in the infrastructure
repository, because this directory must stay portable.

When a limit does exist, headroom is simply `limit - peak working set`. That is
the number to watch: a peak within a few percent of the limit is a stack that
will eventually be killed, no matter what the health status says.

### Kernel evidence

The kernel logs an OOM kill in two lines. The first names the process, the
second names the cgroup:

```text
Memory cgroup out of memory: Killed process 9177 (java) total-vm:4912044kB, anon-rss:507336kB, ...
oom-kill:constraint=CONSTRAINT_MEMCG,...,oom_memcg=/system.slice/docker-<container id>.scope,task=java,pid=9177
```

```bash
dmesg -T | grep -Ei 'oom-kill|out of memory'
journalctl -k --since '2 hours ago' | grep -Ei 'oom-kill|out of memory'
```

Both need root (or `CAP_SYSLOG`), so they may be unavailable to the account that
runs the harness. The second line is the one that identifies the container: the
`oom_memcg` path carries the container id, which is how the harness attributes a
line to a container. The first line alone cannot be attributed.

The counters are more reliable than the log, because they are per cgroup and do
not need a privileged read:

```bash
docker exec <container> cat /sys/fs/cgroup/memory.events
# low 0
# high 0
# max 3
# oom 3
# oom_kill 2
docker exec <container> cat /sys/fs/cgroup/memory/memory.oom_control
# oom_kill_disable 0
# under_oom 0
# oom_kill 36
```

`oom_kill` is cumulative for the container's lifetime, exactly like the restart
count: only its growth across the run window is evidence about the run.

## What every run records

`python -m e2e run` snapshots the stack before the first scenario and again
after the last one, then classifies the window. The block appears in all four
report formats:

| Artifact | Content |
| --- | --- |
| `report.json` | an `environment` object with the verdict, the causes and one record per container |
| `health.json` | the same object on its own: verdict, causes, per-container health, and both raw snapshots |
| `junit.xml` | a zero-test `testsuite name="environment"` with the verdict and one property per container |
| `summary.md` | an `## Environment health` section with a per-container table and the notes |
| `<scenario>/environment.json` | copied into the bundle of a **failed** scenario, so unzipping one failure still shows the environment |

Per container the block records the restart count before and after the run, the
state, the health status, the memory limit, the current and peak working set,
the `oom_kill` counter, and any kernel OOM lines that name the container. For
example:

```json
{
  "verdict": "invalid",
  "summary": "the environment, not the application, is suspect: the stack changed during the run window",
  "reasons": [
    "openhab-garmin-e2e-openhab-stable-1 restarted 1 time(s) during the run (restart count 0 -> 1)",
    "openhab-garmin-e2e-openhab-stable-1 recorded 2 new OOM kill(s) during the run (cgroup memory.events oom_kill)"
  ],
  "containers": [
    {
      "name": "openhab-garmin-e2e-openhab-stable-1",
      "state": "running",
      "health": "healthy",
      "restart_count_before": 0,
      "restart_count_after": 1,
      "restarts_during_run": 1,
      "memory_limit_bytes": null,
      "peak_working_set_bytes": 536870912,
      "peak_source": "cgroup memory.peak",
      "oom_killed": false,
      "oom_kill_count": 2,
      "oom_kills_during_run": 2,
      "notes": ["no container memory limit is set (HostConfig.Memory is 0), so the cgroup limit is the host's"]
    }
  ]
}
```

Note `"health": "healthy"` and `"oom_killed": false` next to two OOM kills: that
is the incident from the introduction, recorded in the artifact.

### Verdicts

| Verdict | Meaning | Exit code |
| --- | --- | --- |
| `valid` | no container restarted, was recreated, was killed, stopped or appeared during the window | `0`, or `1` if a scenario failed |
| `invalid` | the stack changed during the window, so the results are not attributable to the app | `3` |
| `unknown` | the window was not measured (dry run, or no Docker access); nothing is claimed | `0`, or `1` if a scenario failed |

A run is marked `invalid` — not failed — for exactly these causes:

- a restart count grew across the window;
- a restart count shrank, which means the container was recreated;
- the cgroup `oom_kill` counter grew;
- a container is no longer running at the end of the window;
- a container was present in only one of the two snapshots.

Everything else is a note that never changes the verdict: a restart count that
was already non-zero before the window, an `unhealthy` healthcheck at the end, a
missing memory limit, a missing healthcheck, and `State.OOMKilled` being `false`.
Those signals cannot be attributed to the run, and treating them as failures
would mask real app defects.

An invalid run is reported as `invalid` even when a scenario also failed. The
failure is still listed, but it must be re-observed on a healthy stack before it
is treated as an app defect — which is the point of the classification.

## Capturing health for a run inside the stack

The `runner` container has no Docker socket by design (see
[`extended/openhab-restart.yaml`](../scenarios/extended/openhab-restart.yaml)),
so a run executed with `docker compose exec runner` cannot read the stack's
state itself. In that case its block is `unknown`, and the window is captured on
the host instead:

```bash
cd tests/runner
python -m e2e health-snapshot --phase before --output health-window.json

# run the suite inside the stack
docker compose -f ../compose/compose.yaml exec runner \
  python -m e2e run --all

# bring the artifacts out and finish the window
docker compose -f ../compose/compose.yaml cp runner:/artifacts ./artifacts
python -m e2e health-snapshot --phase after \
  --output health-window.json --artifacts ./artifacts
```

The `--phase after` step classifies the window, prints the block, re-renders
`report.json`, `junit.xml`, `summary.md` and `health.json` in the artifacts
directory, and exits `3` when the run is invalid. Nothing is re-executed: the
re-render reconstructs the report from `report.json`.

When the harness runs where Docker is reachable — the checkout on a Docker host,
or a CI job with the socket — `python -m e2e run` records the window directly
and no snapshot file is needed. `python -m e2e status` prints the same numbers
one-shot, which is the quickest way to check a stack before a run.

## Triage procedure for a failed run

1. Read the verdict first: `health.json`, the `environment` object in
   `report.json`, or the `## Environment health` section of `summary.md`.
1. **`invalid`** — do not debug the app. Read the causes: they name the container
   and the counter that changed. Recreate the stack and re-run. If the same
   scenario fails again on a `valid` run, it is worth investigating.
1. **`unknown`** — the environment is unverified. Capture a window around a
   re-run with the commands above, or run `python -m e2e status` before and
   after the run and compare the restart counts by hand.
1. **`valid` and a scenario failed** — this is the app-defect path. Use the
   per-scenario artifacts: the shim trace, the `E2E:` trace events and the
   screenshots. The environment block stays in the report as evidence that the
   failure was not environmental.
1. **`valid` but the numbers look wrong** — check the peak working set against
   the limit (headroom), the restart counts from earlier runs, and `dmesg` for
   kills that predate the window. A container with no limit and a peak at the
   host's memory is a stack waiting to be killed, even if this run was valid.

When the restart count is climbing across runs, stop re-running and collect the
evidence first:

```bash
docker compose -p openhab-garmin-e2e ps -q |
  xargs docker inspect --format '{{.Name}} restarts={{.RestartCount}} started={{.State.StartedAt}}'
docker compose -p openhab-garmin-e2e ps -q |
  xargs -I{} docker exec {} cat /sys/fs/cgroup/memory.events
```

## Limits of this diagnostic

- The verdict describes the environment, not the app. `valid` means "the stack
  did not change", not "the app is correct".
- Only deltas count. Restarts before the window are reported but not acted on.
- Kernel evidence is best-effort: `dmesg` may be unreadable, and a container
  without a `cat` binary or a cgroup v2 filesystem yields no counters. The block
  says so instead of guessing.
- The classification has no heuristics: a short list of deterministic causes,
  each naming its evidence.

## Where the code lives

- [`e2e/health.py`](../runner/e2e/health.py) — parsing, the Docker probe and the
  classification.
- [`e2e/reporting.py`](../runner/e2e/reporting.py) — the artifact, JUnit and
  Markdown renderers.
- [`runner/tests/test_health.py`](../runner/tests/test_health.py) — the recorded
  fixtures in `runner/tests/fixtures/health/` cover both parsers and every
  verdict.
