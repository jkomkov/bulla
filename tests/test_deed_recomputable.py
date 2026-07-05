"""The deed is a *recomputable* certificate — these tests ARE that property.

Bulla's canonicity = determinism over pinned inputs: anyone re-runs `f` and
converges on the same content hash. A read of the code concluding "it's pure"
is the same epistemic move that once missed `source_path` (it leaked through the
certificate BODY, not the math). So the claims are proven here adversarially —
vary the environment / the pack stack and assert the hash does not move.

`deed = f(composition@h, algorithm@v)` — composition pinned, algorithm pinned,
nothing else. The cross-pack test is the load-bearing one: it proves packs/registry
are NOT verdict inputs (if they ever became one, a fee could shift silently).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import bulla
from bulla._canonical import ALGORITHM_VERSION
from bulla.certificate import (
    _compute_certificate_content_hash,
    _content_hash_preimage,
    certify,
    to_dict,
)
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _comp() -> Composition:
    """A fixed composition with a hidden seam AND a dimension that lives in the
    financial pack (`amount_unit`) — so if certify ever consulted the pack
    taxonomy, the cross-pack test below would catch it."""
    a = ToolSpec(name="fs__read", internal_state=("path", "amount"),
                 observable_schema=(("path", "string"),))
    b = ToolSpec(name="pay__send", internal_state=("path", "amount"),
                 observable_schema=(("path", "string"),))
    edge = Edge(from_tool="fs__read", to_tool="pay__send", dimensions=(
        SemanticDimension(name="path_convention", from_field="path", to_field="path"),
        SemanticDimension(name="amount_unit", from_field="amount", to_field="amount"),
    ))
    return Composition(name="recompute-fixture", tools=(a, b), edges=(edge,))


def _hash(comp, **kw) -> str:
    return to_dict(certify(comp, **kw))["certificate_content_hash"]


# ── the load-bearing one: packs/registry are NOT verdict inputs ──────

def test_hash_invariant_across_pack_versions():
    """THE test that *is* the formula. Certify one fixed composition under two
    different global pack stacks; the content hash must be byte-identical — proving
    the verdict carries its own dimensions and `packs@h`/`registry@h` are not deed
    inputs. If `diagnose`/`certify` ever reads live pack/registry state, this fails
    (a fee shifting under a pack change is verdict drift, not an env leak)."""
    from bulla.infer.classifier import configure_packs

    comp = _comp()
    financial = Path(bulla.__file__).resolve().parent / "packs" / "financial.yaml"
    assert financial.exists(), financial
    try:
        configure_packs([])                       # base + community
        h_base = _hash(comp)
        configure_packs([financial])              # base + community + financial
        h_financial = _hash(comp)
        assert h_base == h_financial, (
            "deed hash changed under a different pack stack — packs ARE a verdict "
            "input; deed = f(composition@h, algorithm@v) is false"
        )
    finally:
        configure_packs()                          # restore default global state


# ── the environment must not leak through the body ───────────────────

def test_hash_identical_under_perturbed_environment(tmp_path):
    """Same composition, perturbed environment (different absolute source_path with
    the same basename; a different cwd; two different wall-clock timestamps across
    the calls) → byte-identical hash. Guards the body-leak path (`source_path` was
    instance #1) and the excluded clock."""
    comp = _comp()
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        h1 = _hash(comp, source_path="/Users/alice/secret/deep/x.yaml")
        os.chdir(SRC)  # a different cwd for the second call
        h2 = _hash(comp, source_path="/var/tmp/other/root/x.yaml")
    finally:
        os.chdir(cwd)
    assert h1 == h2


def test_hash_identical_across_pythonhashseed(tmp_path):
    """PYTHONHASHSEED is fixed at interpreter start, so vary it via SUBPROCESS. A
    standing guard: if a future change leaks Python `set`/`dict` iteration order
    into the hashed content, the two seeds diverge and this fails."""
    script = (
        "from bulla.certificate import certify\n"
        "from bulla.model import Composition, ToolSpec, Edge, SemanticDimension\n"
        "a=ToolSpec(name='fs__read',internal_state=('path','amount'),observable_schema=(('path','string'),))\n"
        "b=ToolSpec(name='pay__send',internal_state=('path','amount'),observable_schema=(('path','string'),))\n"
        "e=Edge(from_tool='fs__read',to_tool='pay__send',dimensions=(\n"
        "  SemanticDimension(name='path_convention',from_field='path',to_field='path'),\n"
        "  SemanticDimension(name='amount_unit',from_field='amount',to_field='amount')))\n"
        "c=Composition(name='recompute-fixture',tools=(a,b),edges=(e,))\n"
        "print(certify(c).certificate_content_hash)\n"
    )

    def run(seed: str) -> str:
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": SRC}
        out = subprocess.run([sys.executable, "-c", script], env=env,
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()

    assert run("0") == run("1")


# ── the deed names its `f` ───────────────────────────────────────────

def test_deed_commits_algorithm_version():
    cert = certify(_comp())
    d = to_dict(cert)
    assert d["algorithm_version"] == ALGORITHM_VERSION
    assert "algorithm_version" in _content_hash_preimage(d)  # committed, not excluded
    # the address tracks `f`: a verdict-affecting bump changes the hash
    import dataclasses
    bumped = _compute_certificate_content_hash(
        dataclasses.replace(cert, algorithm_version="2"))
    assert bumped != d["certificate_content_hash"]
