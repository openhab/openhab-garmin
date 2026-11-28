"""Runner image and entrypoint contract.

Three defects from the live runs live in these two files and none of them needs
Docker to keep it fixed:

- the headless JRE had no ``libharfbuzz``, so every ``monkeyc`` invocation died
  with ``UnsatisfiedLinkError`` when it loaded the AWT font manager;
- ``monkeyc`` rewrites ``$SDK/bin/default.jungle``, so a read-only SDK mount at
  the path the compiler uses cannot work. The entrypoint copies the mount to a
  writable path instead of making the mount writable;
- ``CIQ_SDK_ROOT`` was only exported by the entrypoint, so ``docker exec`` (new
  process from the image config) never saw it and every CLI command failed with
  "Connect IQ SDK not configured". The default has to be in the image.
"""

from __future__ import annotations

import re

from e2e import compose as compose_module
from e2e.paths import runner_root

DOCKERFILE = runner_root() / "Dockerfile"
ENTRYPOINT = runner_root() / "entrypoint.sh"


def _dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def _entrypoint() -> str:
    return ENTRYPOINT.read_text(encoding="utf-8")


def _image_env() -> dict:
    """Parse the Dockerfile's ``ENV`` values, following line continuations."""
    values = {}
    buffer = ""
    for line in _dockerfile().splitlines():
        stripped = line.strip()
        if not buffer:
            if not stripped.startswith("ENV "):
                continue
            buffer = stripped[len("ENV ") :]
        else:
            buffer += " " + stripped
        if buffer.endswith("\\"):
            buffer = buffer[:-1]
            continue
        for match in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)=("[^"]*"|\S+)', buffer):
            values[match.group(1)] = match.group(2).strip('"')
        buffer = ""
    return values


def test_font_and_key_packages_are_installed() -> None:
    text = _dockerfile()
    for package in (
        "libharfbuzz0b",
        "libfreetype6",
        "libfontconfig1",
        "fontconfig",
        "openssl",
        "default-jre",
        # Wine, the fallback driver, and the native simulator's runtime. Both
        # sets must be in one image while the fallback exists.
        "wine",
        "wine64",
        "libgtk-3-0",
        "libwebkit2gtk-4.0-37",
        "libsecret-1-0",
        "libnotify4",
        "libusb-1.0-0",
        "librsvg2-common",
        "x11-xkb-utils",
    ):
        assert re.search(rf"^\s+{re.escape(package)}(?::\w+)? \\$", text, re.MULTILINE), package


def test_the_jre_carries_awt_for_the_svg_rasteriser() -> None:
    """monkeyc rasterises SVG through Batik, which initialises AWT.

    On 22.04 ``libawt_xawt.so`` is only in the non-headless JRE; the headless
    package made every resource compile fail with UnsatisfiedLinkError. The
    build runs the shared check, which covers both the font manager and AWT.
    """
    text = _dockerfile()
    assert re.search(r"^\s+default-jre \\$", text, re.MULTILINE)
    assert not re.search(r"^\s+default-jre-headless \\$", text, re.MULTILINE)
    assert "libawt_xawt" in text
    assert "fontmanager_check.py" in text


def test_the_pixbuf_cache_is_regenerated_for_the_native_simulator() -> None:
    """A stale loader cache aborts the native build's first draw on an SVG."""
    text = _dockerfile()
    assert "gdk-pixbuf-query-loaders" in text
    assert "loaders.cache" in text


def test_the_image_smoke_checks_both_drivers() -> None:
    """A missing Wine or GTK/WebKit piece must fail the build, not a launch."""
    text = _dockerfile()
    smoke = text[text.index("RUN wine --version") :]
    assert "libgtk-3.so.0" in smoke
    assert "libwebkit2gtk-4.0.so.37" in smoke
    assert "loaders.cache" in smoke


def test_the_base_image_is_22_for_the_native_simulator() -> None:
    """24.04 dropped webkit2gtk-4.0, so the native binary cannot run there."""
    text = _dockerfile()
    assert re.search(r"^ARG RUNNER_BASE_IMAGE=ubuntu:22\.04$", text, re.MULTILINE)
    assert compose_module.openhab_images()["runner_base"].tag == "22.04"


def test_image_fails_when_libfontmanager_cannot_resolve() -> None:
    text = _dockerfile()
    # The check is a script, not an inline `grep`: plain ldd reports libjvm.so
    # as missing even on a healthy image (the JVM resolves it at run time), so
    # the decision rule lives in e2e/fontmanager.py and is unit-tested.
    assert "fontmanager_check.py" in text
    assert "COPY runner/e2e/fontmanager.py" in text
    assert "python3 /usr/local/bin/fontmanager_check.py" in text
    assert 'grep -q "not found"' not in text


def test_dockerfile_artifacts_default_matches_compose() -> None:
    runner = compose_module.load_compose()["services"]["runner"]
    environment = compose_module._as_mapping(runner.get("environment"))
    assert _image_env()["E2E_ARTIFACTS"] == environment["E2E_ARTIFACTS"]


def test_image_declares_the_staged_sdk_root() -> None:
    """`docker exec` never sees the entrypoint's export, so the image needs it.

    The two values must not drift: the entrypoint stages the copy at
    CIQ_SDK_WORKDIR, and every process in the container resolves the SDK
    through CIQ_SDK_ROOT.
    """
    env = _image_env()
    assert env["CIQ_SDK_ROOT"] == env["CIQ_SDK_WORKDIR"]
    assert env["CIQ_SDK_ROOT"] == "/tmp/ciq-sdk"


def test_entrypoint_copies_the_sdk_out_of_the_read_only_mount() -> None:
    text = _entrypoint()
    assert 'sdk_workdir="${CIQ_SDK_WORKDIR:-/tmp/ciq-sdk}"' in text
    assert 'sdk_source="${CIQ_SDK_MOUNT:-${CIQ_SDK_ROOT:-}}"' in text
    assert 'cp -a "$sdk_source" "$sdk_workdir"' in text
    assert 'export CIQ_SDK_ROOT="$sdk_workdir"' in text
    # The copy has to happen before anything compiles or validates the SDK.
    assert text.index('cp -a "$sdk_source"') < text.index("python -m e2e sdk-check")


def test_entrypoint_reuses_an_existing_copy_without_a_mount() -> None:
    text = _entrypoint()
    # The image default makes CIQ_SDK_ROOT equal CIQ_SDK_WORKDIR; a restart
    # must reuse the copy instead of failing or copying the directory onto
    # itself, and an empty copy must fail with the mount hint.
    assert "reusing the staged Connect IQ SDK" in text
    assert "is empty" in text


def test_entrypoint_never_writes_to_the_sdk_mount() -> None:
    text = _entrypoint()
    # The only mutating commands in the SDK section target the copy, never the
    # read-only mount; the single copy reads the mount and writes the workdir.
    assert 'rm -rf "$sdk_workdir"' in text
    assert 'rm -rf "$sdk_source"' not in text
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("rm ", "mkdir ", "chmod ", "chown ")):
            assert '"$sdk_source"' not in stripped, stripped
        if stripped.startswith("cp "):
            assert '"$sdk_source"' in stripped and '"$sdk_workdir"' in stripped, stripped


def test_entrypoint_links_devices_before_checking_the_sdk() -> None:
    text = _entrypoint()
    assert "python -m e2e link-devices" in text
    assert text.index("python -m e2e link-devices") < text.index("python -m e2e sdk-check")


def test_entrypoint_publishes_readiness_only_after_staging() -> None:
    """`healthy` must mean "staged", not "container started".

    The SDK copy is ~1 GB; the VM once saw the runner report healthy mid-copy
    and the documented `up -d` then `exec` workflow failed with "does not look
    like a Connect IQ SDK".
    """
    text = _entrypoint()
    assert 'ready_marker="${CIQ_READY_MARKER:-/run/ciq-ready}"' in text
    reset = text.index('rm -f "$ready_marker"')
    written = text.index('> "$ready_marker"')
    checks = text.index("python -m e2e sdk-check")
    assert reset < checks < written, "the marker is cleared first and written last"
    assert "simulator.windows=" in text and "simulator.native=" in text


def test_the_runner_healthcheck_requires_the_readiness_marker() -> None:
    runner = compose_module.load_compose()["services"]["runner"]
    healthcheck = runner["healthcheck"]
    command = " ".join(healthcheck["test"])
    assert "ciq-ready" in command
    assert "import e2e" in command
    # At least one simulator build must be staged for the runner to run.
    assert "simulator" in command
    assert healthcheck["start_period"] == "300s"


def test_entrypoint_clears_a_stale_x_socket_before_starting_xvfb() -> None:
    """`docker restart` keeps the container filesystem, so the previous Xvfb's
    socket and lock survive with no server behind them and block a new Xvfb.

    They must only be removed after `xdpyinfo` says nothing is answering, so a
    live server is never disturbed.
    """
    text = _entrypoint()
    guard = text.index('if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then')
    stale_socket = text.index('rm -f "/tmp/.X11-unix/X${display_number}"')
    assert guard < stale_socket, "the socket must not be removed while a server answers"
    assert '"/tmp/.X${display_number}-lock"' in text
    assert 'Xvfb "$DISPLAY"' in text


def test_entrypoint_starts_the_display_before_staging_the_sdk() -> None:
    """Staging copies ~250 MB and validates the SDK.

    Starting Xvfb afterwards makes "container healthy" and "display available"
    mean different things, and a UI driver launched straight after `up` sees a
    display failure that is only slow staging.
    """
    text = _entrypoint()
    assert text.index('Xvfb "$DISPLAY"') < text.index("staging the Connect IQ SDK:")


def test_entrypoint_fails_when_the_display_never_comes_up() -> None:
    text = _entrypoint()
    marker = 'if [ "$display_ready" != "true" ]; then'
    assert marker in text
    failure = text[text.index(marker) :]
    assert "exit 78" in failure
    assert "Xvfb did not start" in failure


def test_entrypoint_records_its_hash_before_anything_expensive() -> None:
    """The script is baked, the harness is mounted, so the two can diverge.

    #412 records the hash the running image executed, and `e2e.entrypoint`
    compares it with the mounted copy (surfaced by `preflight docker.entrypoint`
    and `sdk-check`). The marker has to be written before the checks that
    produced the misleading failure, so a stale image is named as such.
    """
    text = _entrypoint()
    assert "record_entrypoint_hash()" in text
    assert "record_entrypoint_hash\n" in text
    assert "ciq-entrypoint.sha256" in text
    marker = text.index("\nrecord_entrypoint_hash\n")
    assert marker < text.index("python -m e2e link-fonts")
    assert marker < text.index("python -m e2e sdk-check")

