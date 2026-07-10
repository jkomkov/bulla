"""The package version has ONE source of truth.

``__version__`` is stamped into every receipt's ``producer`` provenance, so a
second, drift-prone source would be a provenance defect in a provenance tool.
pyproject declares the version ``dynamic`` and hatch reads it from
``src/bulla/__init__.py``; this test asserts the installed distribution metadata
agrees with the module, so a build can't ship a wheel whose metadata disagrees
with the code (and ``publish.yml`` additionally gates that version == the tag).
"""

from __future__ import annotations

import importlib.metadata

import bulla


def test_metadata_matches_dunder_version() -> None:
    assert importlib.metadata.version("bulla") == bulla.__version__
