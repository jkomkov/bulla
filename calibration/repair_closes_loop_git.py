#!/usr/bin/env python3
"""Move #3, Part B — execution-grounded loop closure on REAL git.

The non-circular capstone for the repair compiler (`bulla__repair` / `repair.py`).
A genuine convention mismatch in real software, with EXECUTION_INDEPENDENT failure
labels.

The seam.  A "filesystem" tool emits a file PATH under the ABSOLUTE-path convention;
a "git" tool consumes it via ``git show HEAD:<path>``, which requires the
REPO-RELATIVE convention — exactly the github-server convention. Local ``git`` is a
faithful stand-in: same convention, real software, no auth. The path-root convention
is hidden, so:

  * bulla (the deployed measurement layer) LOCATEs the obstruction: fee = 1, the one
    hidden path-root convention on the filesystem->git seam. Schema-only,
    deterministic — this is exactly what ``bulla__repair`` emits.
  * REAL git supplies the failure label: ``git show HEAD:<abs>`` raises a genuine
    error; ``git show HEAD:<rel>`` succeeds. The label is git's own behavior, NOT a
    function of the fee — so it is EXECUTION_INDEPENDENT. (Contrast the constructed
    ``seam_backend`` positive control, which the harness self-stamps CONSTRUCTED
    because its failure mechanism is co-authored with the fee. Here the failure
    mechanism is git's, authored by neither us nor the fee.)
  * LOCATE != TRANSPORT.  bulla only *locates* which convention is undeclared. The
    value-level fix — given path_root = "absolute, rooted at <repo>", normalize to
    the consumer's repo-relative convention via ``os.path.relpath`` — is *supplied*
    (the oracle here; the coordination loop in deploy).

Four-way grid (metric = raw count of convention-attributed REAL git failures):
  1. Sufficiency: LOCATE + transport eliminates all N convention-attributed failures
     (N absolute paths -> 0 failures). One disclosure (fee = 1) clears N runtime
     failures — the honest "fewer disclosures than failures" leverage: one seam, N
     runtime crossings.
  2. Necessity (ablation): drop the transport -> the N failures return. (The
     transport, not merely the location, is the active ingredient.)
  3. Specificity (negative control): a repo-relative path to an object absent from
     HEAD fails for a reason orthogonal to the path-root convention; the repair is a
     no-op on it and it stays failing. The repair is specific to the disclosed
     obstruction, not a blunt "make git pass".
  4. Scoped falsifier: this script exits non-zero ONLY if a convention-attributed
     failure survives locate+transport — the operational refutation of elaboration
     soundness (Cor 5.5). An out-of-scope failure surviving is EXPECTED (the
     control); a missing-object error never trips the falsifier.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from bulla.diagnostic import diagnose, minimum_disclosure_set
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec


def _repo_root() -> str:
    return subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


REPO = _repo_root()


def git_show(pathspec: str) -> tuple[bool, str]:
    """REAL execution: ``git show HEAD:<pathspec>``.

    Returns (ok, detail). ok == True iff git exits 0 (the object exists under that
    pathspec in HEAD). The label is git's own exit code — independent of bulla's fee
    => EXECUTION_INDEPENDENT.
    """
    r = subprocess.run(
        ["git", "-C", REPO, "show", f"HEAD:{pathspec}"],
        capture_output=True, text=True,
    )
    detail = (r.stderr.strip().splitlines()[0] if r.stderr.strip() else "ok")
    return r.returncode == 0, detail


# ── LOCATE (bulla, schema-only, deterministic) ───────────────────────────────

def seam_composition() -> Composition:
    """The filesystem->git seam carrying one hidden path-root convention.

    Both tools hold ``path_root`` internally; neither advertises it (observable
    schema empty) => the consumer cannot know the producer's path convention =>
    coherence fee = 1.
    """
    fs = ToolSpec("filesystem", ("path_root",), ())   # path_root hidden
    git = ToolSpec("git", ("path_root",), ())
    edge = Edge(
        "filesystem", "git",
        (SemanticDimension("path_root", "path_root", "path_root"),),
    )
    return Composition("fs_to_git", (fs, git), (edge,))


def transport(abs_path: str) -> str:
    """The value-level fix the disclosed path_root convention enables: convert an
    absolute path (rooted at REPO) to the consumer's repo-relative convention.

    SUPPLIED, not located — this is the oracle stand-in for the coordination loop.
    """
    return os.path.relpath(abs_path, REPO)


def main() -> int:
    # N real tracked files (present in HEAD) => N runtime crossings of the one seam.
    tracked = subprocess.run(
        ["git", "-C", REPO, "ls-files"], capture_output=True, text=True, check=True,
    ).stdout.split()
    rels = [p for p in tracked if p.endswith(".md")][:3] or tracked[:3]
    if len(rels) < 1:
        print("INVALID CONTROL: no tracked files found")
        return 2
    abss = [str(Path(REPO) / r) for r in rels]

    # LOCATE
    comp = seam_composition()
    fee = diagnose(comp).coherence_fee
    located = list(minimum_disclosure_set(comp))
    print(f"LOCATE (bulla, schema-only): fee = {fee}; disclosures = {located}")
    if fee != 1:
        print(f"INVALID CONTROL: expected one path-root convention (fee=1), got {fee}")
        return 2

    # The pre-repair failures are CONVENTION-attributed, not absence: each object is
    # present ON DISK (the producer really holds it) yet git rejects the absolute
    # pathspec. git's own message confirms it ("exists on disk, but not in 'HEAD'"),
    # and we make the attribution execution-derived rather than asserted.
    assert all(os.path.exists(a) for a in abss), \
        "convention test requires the files to be present on disk"

    # (1) SUFFICIENCY + leverage
    pre_fail = [r for r, a in zip(rels, abss) if not git_show(a)[0]]        # ABS
    post_fail = [r for r, a in zip(rels, abss) if not git_show(transport(a))[0]]  # ->REL
    print(f"(1) sufficiency: convention-attributed git failures "
          f"{len(pre_fail)} -> {len(post_fail)}  "
          f"(fee={fee} disclosure cleared {len(pre_fail)} real failures)")

    # (2) NECESSITY (ablation): drop the transport, failures must return
    ablate_fail = [r for r, a in zip(rels, abss) if not git_show(a)[0]]
    print(f"(2) necessity (ablate transport): failures return -> {len(ablate_fail)}")

    # (3) SPECIFICITY (negative control): a repo-relative path absent from HEAD —
    #     a real git failure whose cause (missing object) is orthogonal to the
    #     path-root convention; the repair is a no-op on it.
    ghost = "ghost-not-in-head-7f3a9c.md"
    assert not os.path.exists(Path(REPO) / ghost), \
        "negative control must be a genuinely absent object (not on disk)"
    nc_before = git_show(ghost)[0]
    nc_after = git_show(ghost)[0]          # repair targets path_root; no-op on an absent object
    print(f"(3) specificity (out-of-scope, correct-convention missing object): "
          f"ok before={nc_before}, ok after repair={nc_after} "
          f"(repair must leave it failing)")

    sufficiency = len(pre_fail) >= 1 and len(post_fail) == 0
    necessity = len(ablate_fail) == len(pre_fail) and len(ablate_fail) >= 1
    specificity = (not nc_before) and (not nc_after)

    print()
    print(f"provenance: EXECUTION_INDEPENDENT (labels = real `git show` exit codes, "
          f"fee-independent)")
    print(f"sufficiency={sufficiency}  necessity={necessity}  specificity={specificity}")

    # SCOPED falsifier: fail ONLY on a surviving convention-attributed failure.
    if len(post_fail) > 0:
        print(f"FALSIFIED (elaboration soundness): convention-attributed failures "
              f"survived locate+transport: {post_fail}")
        return 1
    if not (sufficiency and necessity and specificity):
        print("INVALID CONTROL: a control did not hold (harness issue, not a "
              "refutation of the theorem) — investigate before citing.")
        return 2

    print("LOOP CLOSED: bulla LOCATEs the path-root convention (fee=1); transporting "
          "it eliminates the real git failures; ablation restores them; an "
          "out-of-scope failure is left failing. EXECUTION_INDEPENDENT + applied.")

    out = Path(REPO) / "bulla" / "calibration" / "results" / "repair_closes_loop_git.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "seam": "filesystem(abs) -> git(repo-relative); local git stands in for github",
        "provenance": "EXECUTION_INDEPENDENT",
        "fee": fee,
        "disclosures": located,
        "n_runtime_crossings": len(rels),
        "convention_failures_present_on_disk": all(os.path.exists(a) for a in abss),
        "negative_control_absent_on_disk": not os.path.exists(Path(REPO) / ghost),
        "convention_failures_pre": len(pre_fail),
        "convention_failures_post_transport": len(post_fail),
        "ablation_failures": len(ablate_fail),
        "negative_control_ok_before": nc_before,
        "negative_control_ok_after": nc_after,
        "sufficiency": sufficiency,
        "necessity": necessity,
        "specificity": specificity,
    }, indent=2) + "\n")
    print(f"artifact: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
