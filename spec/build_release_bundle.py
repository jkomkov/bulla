#!/usr/bin/env python3
"""Build the frozen implementation-independent ActionReceipt v0.2 zip.

The worktree's shared vector directory may also contain later draft vectors.
The v0.2 bundle filters ``expected.json`` to the vectors actually included so a
future extension cannot make the frozen stranger test reference missing files.
"""

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

HERE = Path(__file__).resolve().parent
OUT = HERE / "dist/action-receipt-v0.2.zip"
FILES = (
    "README.md",
    "action-receipt-v0.2.md",
    "action-receipt-v0.2.schema.json",
    "IMPLEMENTATION-CHECKLIST.md",
    "COMPATIBILITY.md",
    "vectors/independent_check.py",
    "vectors/expected.json",
    "vectors/valid-release.json",
    "vectors/convention-receipt.json",
    "vectors/tampered-convention.json",
    "vectors/tampered-evidence.json",
    "vectors/blank-remedy-anchor.json",
    "vectors/witness-canon2.json",
    "vectors/witness-legacy-v1.json",
)
VECTOR_FILES = {
    Path(relative).name
    for relative in FILES
    if relative.startswith("vectors/") and relative.endswith(".json")
    and relative != "vectors/expected.json"
}

OUT.parent.mkdir(exist_ok=True)
with ZipFile(OUT, "w", ZIP_DEFLATED) as bundle:
    for relative in FILES:
        source = HERE / relative
        if not source.is_file():
            raise SystemExit(f"missing bundle input: {relative}")
        destination = Path("action-receipt-v0.2") / relative
        if relative == "vectors/expected.json":
            expected = json.loads(source.read_text())
            frozen = {name: verdict for name, verdict in expected.items() if name in VECTOR_FILES}
            bundle.writestr(str(destination), json.dumps(frozen, indent=2, sort_keys=True) + "\n")
        else:
            bundle.write(source, destination)
print(OUT)
