"""The simulator's numeric language index.

``simulator.ini`` stores the device language as an **index**, not a code:

.. code-block:: text

    Language=7

A working installation on the maintainer's machine carries ``Language=7``, and
launching with that value makes the app report ``E2E:LANGUAGE;code=deu``. The
index is therefore an enumeration of the simulator's language list, whose order
is not documented and must be enumerated by measurement:

.. code-block:: bash

    python -m e2e language-scan --prg <built.prg> --write

``language-scan`` starts the simulator once per index, reads
``E2E:LANGUAGE;code=...`` from the app and merges the result into
``tests/config/simulator-languages.json``. This module is the **single** source
of that map: the driver, the engine and the coverage document all read it, so a
code that has no measured index is reported as blocked instead of guessed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional, Tuple

#: Checked-in map of Connect IQ language code -> ``simulator.ini`` index.
LANGUAGE_INDEX_FILE = Path(__file__).resolve().parents[2] / "config" / "simulator-languages.json"

#: The language whose index is the simulator's own default: the driver removes
#: ``Language`` instead of writing an index it has not measured. Every guest run
#: so far reported ``eng`` while the key was absent or ignored, so the default is
#: English; ``language-scan`` measures and records the index like any other.
DEFAULT_LANGUAGE = "eng"


class LanguageIndexError(RuntimeError):
    """Raised when the language index map cannot be read or is not a map."""


def load_language_index(path: Optional[Path] = None) -> Dict[str, int]:
    """Read the code -> index map, empty when the file does not exist yet."""
    source = Path(path) if path is not None else LANGUAGE_INDEX_FILE
    if not source.is_file():
        return {}
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except ValueError as error:
        raise LanguageIndexError(f"{source} is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise LanguageIndexError(f"{source} must be a JSON object of code -> index")
    mapping: Dict[str, int] = {}
    for code, index in document.items():
        if isinstance(index, bool) or not isinstance(index, int):
            raise LanguageIndexError(
                f"{source}: language {code!r} has index {index!r}; an integer is required"
            )
        mapping[str(code)] = index
    return mapping


def write_language_index(mapping: Dict[str, int], path: Optional[Path] = None) -> Path:
    """Persist the map in a stable order (by index), for ``language-scan``."""
    target = Path(path) if path is not None else LANGUAGE_INDEX_FILE
    ordered = dict(sorted(mapping.items(), key=lambda item: (item[1], item[0])))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        raise LanguageIndexError(
            f"cannot write {target}: {error}\n"
            "A mounted checkout is often read-only (for example `/work` in the "
            "runner container). Write the map somewhere writable and copy it "
            "back:\n"
            "  python -m e2e language-scan --prg <prg> --write --output /tmp/languages.json\n"
            f"  cp /tmp/languages.json {target}   # from a writable shell"
        ) from error
    return target


def language_index(code: str, mapping: Optional[Dict[str, int]] = None) -> Optional[int]:
    """The ini index for ``code``, or ``None`` when it has not been measured."""
    table = load_language_index() if mapping is None else mapping
    return table.get(code)


def provisionable_languages(
    languages: Tuple[str, ...] = (), mapping: Optional[Dict[str, int]] = None
) -> Tuple[str, ...]:
    """The languages the harness can put the app into.

    ``eng`` is always included because the driver removes the key and relies on
    the simulator's default; every other code needs a measured index.
    """
    table = load_language_index() if mapping is None else mapping
    known = {DEFAULT_LANGUAGE} | set(table)
    if not languages:
        return tuple(sorted(known))
    return tuple(language for language in languages if language in known)
