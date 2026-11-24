"""Runner image and entrypoint contract.

Two defects from the first live run live in these two files and neither needs
Docker to keep them fixed:

- the headless JRE had no ``libharfbuzz``, so every ``monkeyc`` invocation died
  with ``UnsatisfiedLinkError`` when it loaded the AWT font manager;
- ``monkeyc`` rewrites ``$SDK/bin/default.jungle``, so a read-only SDK mount at
  the path the compiler uses cannot work. The entrypoint copies the mount to a
  writable path instead of making the mount writable.
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


def test_font_and_key_packages_are_installed() -> None:
    text = _dockerfile()
    for package in (
        "libharfbuzz0b",
        "libfreetype6",
        "libfontconfig1",
        "fontconfig",
        "openssl",
    ):
        assert re.search(rf"^\s+{re.escape(package)} \\$", text, re.MULTILINE), package


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
    match = re.search(r"^\s*E2E_ARTIFACTS=(\S+)\s*\\$", _dockerfile(), re.MULTILINE)
    assert match, "the image must set a default E2E_ARTIFACTS"
    runner = compose_module.load_compose()["services"]["runner"]
    environment = compose_module._as_mapping(runner.get("environment"))
    assert match.group(1) == environment["E2E_ARTIFACTS"]


def test_entrypoint_copies_the_sdk_out_of_the_read_only_mount() -> None:
    text = _entrypoint()
    assert 'sdk_source="${CIQ_SDK_MOUNT:-${CIQ_SDK_ROOT:-}}"' in text
    assert 'cp -a "$sdk_source" "$sdk_workdir"' in text
    assert 'export CIQ_SDK_ROOT="$sdk_workdir"' in text
    # The copy has to happen before anything compiles or validates the SDK.
    assert text.index('cp -a "$sdk_source"') < text.index("python -m e2e sdk-check")


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
