"""Build-time sanity checks for the JRE the runner's compiler needs.

``monkeyc`` generates resources, so it loads AWT even for a headless build. Two
live failures motivated this module:

- a missing ``libharfbuzz`` broke every compile on the first live run;
- on Ubuntu 22.04, ``default-jre-headless`` has no ``libawt_xawt.so``, so
  ``monkeyc``'s SVG rasteriser (Batik initialises AWT) died with
  ``UnsatisfiedLinkError`` after the image had looked healthy.

The first check shipped in #386 was also wrong: plain ``ldd`` reports
``libjvm.so`` as ``not found`` because the JVM resolves it at run time through
its own ``LD_LIBRARY_PATH`` (``$JAVA_HOME/lib`` and ``$JAVA_HOME/lib/server``).
The guest image build failed on a healthy image because of it. This module
measures the same thing the JVM measures: it runs ``ldd`` with that path set and
only reports libraries that are genuinely unresolved.

The module is copied into the runner image and run at build time (the SDK is
never baked in, so a real ``monkeyc`` compile cannot run there). It is plain
stdlib and importable, so the decision rule is unit-tested against recorded
``ldd`` output without Docker.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

#: Library the font check is about: the JRE's AWT font manager.
LIBRARY_NAME = "libfontmanager.so"

#: Library the AWT check is about. Batik's ``CursorManager`` initialises AWT, and
#: AWT on Linux loads this; Ubuntu 22.04 ships it only in the non-headless JRE.
AWT_LIBRARY_NAME = "libawt_xawt.so"

#: Libraries the JVM resolves itself before anything is loaded. ``libjvm.so``
#: lives in ``$JAVA_HOME/lib/server`` and is found through the JVM's own
#: ``LD_LIBRARY_PATH``, so its absence from the static link path is expected.
RUNTIME_RESOLVED = ("libjvm.so",)

#: Default JRE root on the Debian/Ubuntu images the runner is built from.
JVM_ROOT = Path("/usr/lib/jvm")


def environment_for(
    java_home: Path,
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """The loader environment the JVM uses when it loads the font manager."""
    env = dict(os.environ if environ is None else environ)
    paths = [str(Path(java_home) / "lib"), str(Path(java_home) / "lib" / "server")]
    existing = env.get("LD_LIBRARY_PATH")
    if existing:
        paths.append(existing)
    env["LD_LIBRARY_PATH"] = os.pathsep.join(paths)
    return env


def unresolved_libraries(ldd_output: str) -> List[str]:
    """Libraries ``ldd`` could not resolve, minus the runtime-resolved ones.

    Order is preserved and duplicates are dropped, so the message names each
    library once.
    """
    missing: List[str] = []
    for line in ldd_output.splitlines():
        if "not found" not in line:
            continue
        name = line.split("=>", 1)[0].strip().split()
        if not name:
            continue
        library = name[0]
        if library in RUNTIME_RESOLVED:
            continue
        if library not in missing:
            missing.append(library)
    return missing


def discover_java_home(
    environ: Optional[Mapping[str, str]] = None,
    jvm_root: Path = JVM_ROOT,
) -> Optional[Path]:
    """Find the JRE that carries the font manager."""
    env = os.environ if environ is None else environ
    configured = env.get("JAVA_HOME")
    if configured:
        path = Path(configured)
        if (path / "lib" / LIBRARY_NAME).is_file():
            return path
    if jvm_root.is_dir():
        for candidate in sorted(path for path in jvm_root.iterdir() if path.is_dir()):
            if (candidate / "lib" / LIBRARY_NAME).is_file():
                return candidate
    return None


def check_library(
    java_home: Path,
    library_name: str,
    *,
    missing_hint: str,
    runner: Optional[Callable[..., object]] = None,
) -> Tuple[bool, str]:
    """Run ``ldd`` against one JRE library and report the verdict.

    Returns ``(ok, message)``; ``ok`` is False only for a real problem, so the
    image build fails on a broken JRE and not on the JVM's own library
    resolution.
    """
    library = Path(java_home) / "lib" / library_name
    if not library.is_file():
        return False, f"error: {library} does not exist; {missing_hint}"

    run = runner or subprocess.run
    try:
        completed = run(
            ["ldd", str(library)],
            capture_output=True,
            text=True,
            env=environment_for(java_home),
            check=False,
        )
    except OSError as error:
        return False, f"error: cannot run ldd: {error}"

    output = f"{completed.stdout or ''}{completed.stderr or ''}"
    missing = unresolved_libraries(output)
    if missing:
        return False, (
            f"error: {library_name} has unresolved shared libraries: "
            + ", ".join(missing)
            + "\n"
            + output.strip()
        )
    if completed.returncode != 0:
        return False, (
            f"error: ldd failed for {library} (exit {completed.returncode}):\n"
            f"{output.strip()}"
        )
    return True, f"{library} resolves every library the loader needs"


def check(
    java_home: Path,
    *,
    runner: Optional[Callable[..., object]] = None,
) -> Tuple[bool, str]:
    """Check the font manager ``monkeyc`` loads when it generates resources."""
    return check_library(
        java_home,
        LIBRARY_NAME,
        missing_hint=(
            "the JRE layout changed and the font-stack check has to be updated"
        ),
        runner=runner,
    )


def check_awt(
    java_home: Path,
    *,
    runner: Optional[Callable[..., object]] = None,
) -> Tuple[bool, str]:
    """Check ``libawt_xawt.so``, which Batik's AWT initialisation loads.

    Ubuntu 22.04 ships it only in the non-headless JRE. Without the file,
    ``monkeyc`` fails a resource compile with ``UnsatisfiedLinkError`` long
    after the image looked healthy, so the build names it here.
    """
    return check_library(
        java_home,
        AWT_LIBRARY_NAME,
        missing_hint=(
            "the JRE is headless; monkeyc rasterises SVG drawables through "
            "Batik, which initialises AWT. Install default-jre (non-headless), "
            "not default-jre-headless"
        ),
        runner=runner,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fontmanager-check",
        description=(
            "Fail when the JRE's libfontmanager.so or libawt_xawt.so cannot "
            "resolve a shared library monkeyc would need. Run at runner image "
            "build time."
        ),
    )
    parser.add_argument(
        "--java-home",
        help="JRE to check (default: JAVA_HOME, else /usr/lib/jvm)",
    )
    args = parser.parse_args(argv)

    java_home = Path(args.java_home) if args.java_home else discover_java_home()
    if java_home is None:
        print(
            f"error: no JRE with lib/{LIBRARY_NAME} found under {JVM_ROOT}",
            file=sys.stderr,
        )
        return 1

    ok = True
    for label, check_function in (("font stack", check), ("AWT", check_awt)):
        passed, message = check_function(java_home)
        print(f"{label}: {message}", file=sys.stdout if passed else sys.stderr)
        ok = ok and passed
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
