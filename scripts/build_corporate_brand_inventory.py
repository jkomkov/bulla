"""Generate a frozen, path/line-bound inventory of former-name references."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = "70e603401d9876501064b3ed7e4d17838cb0adf7"
OUTPUT = ROOT / "docs/corporate-rebrand-inventory.json"
CURRENT = {
    "README.md", "GOVERNANCE.md", "docs/CAPABILITIES.md",
    "docs/sa-ra.svg", "docs/sa-ra-favicon.svg",
    ".github/ISSUE_TEMPLATE/external_reliance_candidate.yml",
    ".github/ISSUE_TEMPLATE/independent_witness_candidate.yml",
}


def inventory():
    entries = []
    names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", BASE], cwd=ROOT, text=True).splitlines()
    for name in names:
        raw = subprocess.check_output(["git", "show", BASE + ":" + name], cwd=ROOT)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        lines = [i for i, line in enumerate(text.splitlines(), 1) if re.search(r"glyph[ -]?standard|\bGlyph\b", line, re.I)]
        if not lines:
            continue
        category = "historical evidence"
        if name.startswith(("src/", "spec/", "examples/", "compositions/", "policies/")):
            category = "compatibility identifier"
        if name.startswith(("scripts/", "tests/", ".github/")):
            category = "internal infrastructure"
        if name in CURRENT:
            category = "current branding"
        entries.append({"path": name, "category": category, "baseline_sha256": hashlib.sha256(raw).hexdigest(), "lines": lines})
    return {"baseline_source": BASE, "legal_identity_basis": "Owner confirmed effective rename of the same corporation; no ownership or authority change.", "pattern": "glyph[ -]?standard or standalone Glyph, case insensitive; UTF-8 tracked sources", "entries": entries}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    raw = json.dumps(inventory(), indent=2) + "\n"
    if args.write:
        OUTPUT.write_text(raw, encoding="utf-8")
    else:
        assert OUTPUT.read_text(encoding="utf-8") == raw
    print("Corporate brand inventory: baseline references classified")
