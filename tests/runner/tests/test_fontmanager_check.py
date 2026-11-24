"""Decision rule of the runner image's font-stack smoke check.

The check itself only runs during the Docker image build, which CI cannot do,
so it is tested here against `ldd` output recorded on the guest that ran the
first live stack. That matters: the check shipped in #386 was never executed in
its real environment and failed there on a healthy image, because plain `ldd`
reports `libjvm.so` as missing (the JVM resolves it at run time through its own
`LD_LIBRARY_PATH`).

The recorded outputs below are the guest's, verbatim.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from e2e.fontmanager import (
    check,
    discover_java_home,
    environment_for,
    unresolved_libraries,
)


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


#: `ldd "$JAVA_HOME/lib/libfontmanager.so"` on the guest, no LD_LIBRARY_PATH.
PLAIN_LDD = """\
\tlibharfbuzz.so.0 => not found
\tlibjvm.so => not found
\tlibjvm.so => not found
\tlibjvm.so => not found
"""

#: The same call with LD_LIBRARY_PATH=$JAVA_HOME/lib:$JAVA_HOME/lib/server.
JVM_PATH_LDD = """\
\tlibharfbuzz.so.0 => not found
\tlibjvm.so => /usr/lib/jvm/java-21-openjdk-amd64/lib/server/libjvm.so (0x00007f2c4c000000)
"""

#: A correctly provisioned image.
HEALTHY_LDD = """\
\tlibharfbuzz.so.0 => /lib/x86_64-linux-gnu/libharfbuzz.so.0 (0x00007f2c4b000000)
\tlibfreetype.so.6 => /lib/x86_64-linux-gnu/libfreetype.so.6 (0x00007f2c4b100000)
\tlibfontconfig.so.1 => /lib/x86_64-linux-gnu/libfontconfig.so.1 (0x00007f2c4b200000)
\tlibjvm.so => /usr/lib/jvm/java-21-openjdk-amd64/lib/server/libjvm.so (0x00007f2c4c000000)
"""


def test_libjvm_alone_is_not_a_failure() -> None:
    """This is what failed the build on a healthy image in the live run."""
    assert unresolved_libraries("\tlibjvm.so => not found\n") == []


def test_the_real_failure_is_still_reported() -> None:
    assert unresolved_libraries(JVM_PATH_LDD) == ["libharfbuzz.so.0"]


def test_plain_ldd_reports_only_the_genuine_problem() -> None:
    assert unresolved_libraries(PLAIN_LDD) == ["libharfbuzz.so.0"]


def test_duplicates_are_collapsed() -> None:
    output = "\tlibharfbuzz.so.0 => not found\n\tlibharfbuzz.so.0 => not found\n"
    assert unresolved_libraries(output) == ["libharfbuzz.so.0"]


def test_healthy_output_is_clean() -> None:
    assert unresolved_libraries(HEALTHY_LDD) == []


def test_loader_path_matches_the_jvm(tmp_path: Path) -> None:
    env = environment_for(tmp_path)
    parts = env["LD_LIBRARY_PATH"].split(os.pathsep)
    assert parts[0] == str(tmp_path / "lib")
    assert parts[1] == str(tmp_path / "lib" / "server")


def test_loader_path_keeps_an_existing_entry(tmp_path: Path) -> None:
    env = environment_for(tmp_path, {"LD_LIBRARY_PATH": "/opt/extra"})
    assert env["LD_LIBRARY_PATH"].split(os.pathsep)[-1] == "/opt/extra"


class _Recorder:
    """Records the ldd invocation and returns a canned result."""

    def __init__(self, result: _Completed) -> None:
        self.result = result
        self.calls: list[dict] = []

    def __call__(self, command, **kwargs):  # noqa: ANN001 - subprocess signature
        self.calls.append({"command": list(command), **kwargs})
        return self.result


def _jre(tmp_path: Path) -> Path:
    java_home = tmp_path / "jre"
    (java_home / "lib").mkdir(parents=True)
    (java_home / "lib" / "libfontmanager.so").write_bytes(b"\x7fELF")
    return java_home


def test_check_passes_when_only_libjvm_is_missing(tmp_path: Path) -> None:
    recorder = _Recorder(_Completed(stdout="\tlibjvm.so => not found\n"))
    ok, message = check(_jre(tmp_path), runner=recorder)
    assert ok, message
    assert recorder.calls[0]["env"]["LD_LIBRARY_PATH"].count("lib") >= 2
    assert "libjvm.so" not in message


def test_check_fails_when_harfbuzz_is_missing(tmp_path: Path) -> None:
    recorder = _Recorder(_Completed(stdout=JVM_PATH_LDD))
    ok, message = check(_jre(tmp_path), runner=recorder)
    assert not ok
    assert message.splitlines()[0].endswith("libharfbuzz.so.0")
    assert "libjvm.so => not found" not in message


def test_check_reports_a_missing_font_manager(tmp_path: Path) -> None:
    recorder = _Recorder(_Completed())
    ok, message = check(tmp_path / "absent", runner=recorder)
    assert not ok
    assert "libfontmanager.so" in message
    assert recorder.calls == []


def test_check_reports_ldd_failing(tmp_path: Path) -> None:
    recorder = _Recorder(_Completed(returncode=1, stderr="ldd: cannot read\n"))
    ok, message = check(_jre(tmp_path), runner=recorder)
    assert not ok
    assert "ldd failed" in message


def test_java_home_is_discovered_from_the_environment(tmp_path: Path) -> None:
    java_home = _jre(tmp_path)
    assert discover_java_home({"JAVA_HOME": str(java_home)}) == java_home


def test_java_home_discovery_falls_back_to_the_jvm_root(tmp_path: Path) -> None:
    java_home = _jre(tmp_path / "usr-lib-jvm")
    assert discover_java_home({}, jvm_root=java_home.parent) == java_home


def test_missing_environment_and_no_jvm_root_is_reported(tmp_path: Path) -> None:
    assert discover_java_home({}, jvm_root=tmp_path / "absent") is None
