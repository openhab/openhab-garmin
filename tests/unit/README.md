# Connect IQ unit tests

The pilot of the Connect IQ (Run No Evil) unit-test tier. It covers the
protocol and logic code that the E2E harness cannot reach while the simulator
cannot be driven; see [`../docs/unit-tests.md`](../docs/unit-tests.md) for the
design note, the evidence, the limits and the operator commands.

```bash
tests/unit/run --help
tests/unit/run                       # all tests on fenix847mm
tests/unit/run --test SitemapModelTests.labelStateIsSplitFromLabel
```

The test sources themselves are in `../../source/test/`, not here: they are part
of the app's source path so that every device build typechecks them. This folder
holds only the runner and its result parser.

No Docker, no openHAB and no shim. The SDK is never committed; point
`CIQ_SDK_ROOT` at the pinned 9.3.0 SDK (see [`../docs/sdk.md`](../docs/sdk.md)).
