# Shim: HTTP proxy and fault injector

The shim sits between the app and openHAB. It records every request and can replace
a response with a scripted fault. It has no third-party dependencies: the image runs
exactly the code in `shim/`, using the Python standard library.

```text
app  ->  shim  ->  openHAB
```

## Control API

Never faulted, never proxied, all under `/__shim__`:

| Endpoint | Purpose |
| --- | --- |
| `GET /__shim__/health` | `{status, upstream, rules, requests}` |
| `POST /__shim__/script` | install a fault script (JSON body) |
| `GET /__shim__/script` | the installed script |
| `POST /__shim__/reset` | clear script **and** request trace |
| `GET /__shim__/requests` | recorded requests, oldest first |
| `POST /__shim__/requests/reset` | clear the trace, keep the script |

A recorded request looks like:

```json
{
  "seq": 3,
  "method": "GET",
  "path": "/rest/sitemaps/garmin_e2e",
  "request_body": "",
  "headers": {"accept": "application/json"},
  "matched_rule": "unavailable",
  "status": 503,
  "response_bytes": 19,
  "delay_ms": 0,
  "duration_ms": 1.2,
  "reset": false,
  "error": null
}
```

**Only the request is recorded, never the response body.** The fields split
into two groups, and mixing them up is how a fault scenario can look green while
testing nothing:

| Group | Fields | Meaning |
| --- | --- | --- |
| what the app sent | `request_body`, `headers` | a command payload; empty for a `GET` |
| what the shim answered | `status`, `response_bytes`, `matched_rule`, `reset` | the scripted fault's status and length, or the upstream response's |

So "did the app receive the malformed body?" is answered by
`matched_rule: "truncated"` (the named fault was applied) and
`response_bytes`, **not** by a body comparison: `request_body` is what the app
sent, and a `GET` poll sends nothing. Assertions use `matched_rule` or
`faulted` for that reason (see
[scenarios.md](../docs/scenarios.md#asserting-on-an-injected-fault)).

`Authorization` is recorded as `<redacted>`; nothing secret is persisted.

## Fault script format

```json
{
  "version": 1,
  "rules": [
    {
      "name": "unavailable",
      "match": {"method": "GET", "path_prefix": "/rest/sitemaps/"},
      "times": 2,
      "delay_ms": 0,
      "response": {"status": 503, "body": "Service Unavailable"}
    }
  ]
}
```

| Key | Meaning |
| --- | --- |
| `name` | required for readable failure messages; must not be empty |
| `match.method` | `GET`/`POST`/`PUT`/`PATCH`/`DELETE`, case-insensitive |
| `match.path` | exact match on the request target, including any query string |
| `match.path_prefix` | prefix match; cannot be combined with `path` |
| `match.body_contains` | substring of the request body |
| `times` | how often the rule applies; `0` (default) means unlimited |
| `delay_ms` | sleep before answering, `0..60000` |
| `reset` | drop the connection with a TCP RST instead of answering |
| `response.status` | `100..599`, default `200` |
| `response.body` | literal body; `""` is a valid empty body |
| `response.body_size` | generate a valid JSON body of exactly this many bytes (`2..32 MiB`); cannot be combined with `body` |
| `response.content_type` | defaults to `text/plain; charset=utf-8`, or `application/json` for `body_size` |

Rules are evaluated in order; the first rule that matches and still has uses left
wins. Everything else is proxied unchanged. A rule must set either `response` or
`reset: true`, not both.

Unknown keys are rejected on purpose: a typo in a rule name would otherwise turn a
fault scenario into a happy-path scenario that still passes.

The grammar lives in `shim/faults.py` and is imported by the runner too, so the
runner and the shim validate scripts identically and both are covered by the same
unit tests (`tests/runner/tests/test_shim_faults.py`).

## Scenario scripts

Scenarios author the same rules in YAML; the runner normalises them to the JSON
document above. The top-level block is installed **before the app launches**, so
every poll sees the fault:

```yaml
shim:
  rules:
    - name: unavailable
      match:
        method: GET
        path_prefix: /rest/sitemaps/garmin_e2e
      times: 3
      response:
        status: 503
        body: Service Unavailable
```

A scenario that needs a healthy poll first -- for example to reach the
temporary-error path, which only exists while the app holds a fresh sitemap --
installs the same rules mid-run with the `install_faults` step instead. It
replaces any previously installed script, and its failure messages name the
rules it installed.

## Body generation

`body_size` produces a syntactically valid JSON document of exactly the requested
size (`{"id":"shim","padding":"..."}`, or a JSON string for very small sizes). That
matters: an oversized body must fail for _size_, not for being malformed, or the
scenario would be asserting the wrong error path.

## Running it standalone

```bash
docker build -t openhab-garmin-shim -f tests/shim/Dockerfile tests
docker run --rm -p 127.0.0.1:8081:8080 \
  -e OPENHAB_UPSTREAM=http://host.docker.internal:8080 \
  openhab-garmin-shim
```

The port is published on loopback only, matching the Compose stack; expose it
through a reverse proxy if it must be reachable from another machine.

Environment: `OPENHAB_UPSTREAM`, `SHIM_BIND`, `SHIM_PORT`, `SHIM_VERSION`.
