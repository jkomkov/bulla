"""CLI entry point: diagnose and check subcommands."""

from __future__ import annotations

import argparse
import importlib.resources
import json
import sys
from pathlib import Path

import yaml

from bulla import __version__
from bulla.audit_report import (
    audit_report_to_json_dict,
    build_audit_report,
    format_audit_report_text,
    format_verbose_blind_spots,
)
from bulla.certificate import certify, to_dict, to_json
from bulla.diagnostic import diagnose
from bulla.formatters import format_json, format_sarif, format_text
from bulla.model import Composition
from bulla.parser import CompositionError, load_composition
from bulla.regime import format_regime_warning, validate_regime


# Sprint 11 Phase 2: centralized composition loader that surfaces regime
# warnings consistently across all CLI commands. Use this helper anywhere
# a composition is loaded from a user-supplied path; it ensures the
# regime warning fires once per file regardless of which command is invoked.
def _load_with_regime_warning(path) -> "Composition":
    """Load a composition from `path` and emit a regime warning to stderr
    if the composition fails the schema-shape predicate.

    Wraps `bulla.parser.load_composition`. Any malformed YAML still
    raises `CompositionError` from the parser (Layer 1 defense); this
    helper adds the Layer-3 regime warning for compositions that pass
    the parser but trip the projective-observables check.
    """
    comp = load_composition(path)
    # Sprint 12 fix: pass the actual file path so the warning suggests
    # `bulla regime <path>` (not the YAML composition name, which would
    # not work as a CLI argument).
    warning = format_regime_warning(comp, source_path=str(path))
    if warning is not None:
        print(f"\n[{path.name}] {warning}\n", file=sys.stderr)
    return comp


def _add_pack_args(parser: argparse.ArgumentParser) -> None:
    """Add --pack argument to a subcommand parser."""
    parser.add_argument(
        "--pack",
        type=Path,
        action="append",
        default=None,
        dest="packs",
        metavar="FILE",
        help="Additional convention pack YAML (repeatable, later packs override)",
    )


def _configure_packs_from_args(args: argparse.Namespace) -> None:
    """Configure the active pack stack from CLI args if packs were specified."""
    packs = getattr(args, "packs", None)
    if packs:
        from bulla.infer.classifier import configure_packs
        configure_packs(extra_paths=packs)


def _resolve_paths(raw: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for p in raw:
        if p.is_dir():
            paths.extend(sorted(p.glob("*.yaml")))
            paths.extend(sorted(p.glob("*.yml")))
        else:
            paths.append(p)
    return paths


def _examples_dir() -> Path:
    """Locate bundled composition examples."""
    pkg = importlib.resources.files("bulla")
    return Path(str(pkg / "compositions"))


def _cmd_regime(args: argparse.Namespace) -> None:
    """Sprint 11 Phase 6: print the regime classification of compositions.

    Per-composition output (text format):
      - rank_obs / rank_internal / fee_formula
      - is_well_formed_for_fee  (Sprint 8)
      - has_projective_observables  (Sprint 9)
      - has_dfd_conservative / has_chp_conservative / is_exact_regime_conservative (Sprint 11)
      - is_all_hidden / is_all_observable / dominance flags

    JSON format emits a list of `{path, regime}` dicts where each `regime`
    matches the `regime` block of `bulla diagnose --format json`.
    """
    from bulla.regime import classify
    _configure_packs_from_args(args)
    if not args.files:
        print("Error: provide composition files or directories", file=sys.stderr)
        sys.exit(1)
    paths = _resolve_paths(args.files)
    if not paths:
        print("No composition files found.", file=sys.stderr)
        sys.exit(1)
    fmt = getattr(args, "format", "text")

    records: list[dict] = []
    for path in paths:
        try:
            comp = _load_with_regime_warning(path)
        except CompositionError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        report = classify(comp)
        record = {
            "path": str(path),
            "name": comp.name,
            "rank_obs": report.rank_obs,
            "rank_internal": report.rank_internal,
            "fee_formula": report.fee_formula,
            "is_well_formed_for_fee": report.is_well_formed_for_fee,
            "has_projective_observables": report.has_projective_observables,
            "has_dfd_conservative": report.has_dfd_conservative,
            "has_chp_conservative": report.has_chp_conservative,
            "is_exact_regime_conservative": report.is_exact_regime_conservative,
            "is_all_hidden": report.is_all_hidden,
            "is_all_observable": report.is_all_observable,
            "has_internal_dominance": report.has_internal_dominance,
            "has_balanced_ranks": report.has_balanced_ranks,
            "has_obs_dominance": report.has_obs_dominance,
        }
        records.append(record)

    if fmt == "json":
        print(json.dumps(records if len(records) > 1 else records[0], indent=2))
        return

    # Text format
    for record in records:
        print(f"\n{record['name']}  ({record['path']})")
        print(f"  rank_obs:                       {record['rank_obs']}")
        print(f"  rank_internal:                  {record['rank_internal']}")
        print(f"  fee_formula:                    {record['fee_formula']}")
        print(f"  is_well_formed_for_fee:         {record['is_well_formed_for_fee']}")
        print(f"  has_projective_observables:     {record['has_projective_observables']}")
        print(f"  has_dfd_conservative:           {record['has_dfd_conservative']}")
        print(f"  has_chp_conservative:           {record['has_chp_conservative']}")
        print(f"  is_exact_regime_conservative:   {record['is_exact_regime_conservative']}")
        print(f"  is_all_hidden:                  {record['is_all_hidden']}")
        if record['has_obs_dominance']:
            print(f"  ⚠ has_obs_dominance (fee < 0):  {record['has_obs_dominance']}  "
                  f"— see bulla/docs/REGIME.md")


# Sprint 13 — `bulla certify` is the per-composition certificate
# orchestrator: bundles regime + diagnostic + cross-server + witness
# geometry + interpretation labels into one JSON artifact per composition.
# See bulla.certificate.CompositionCertificate and bulla/docs/REGIME.md
# for the full schema.

# The 10-composition seed set lives at module scope so it can be
# loaded by name in --seed-set mode without re-parsing each invocation.
_SEED_SET_REGISTRY_PAIRS = (
    ("filesystem", "github"),
    ("github", "notion"),
)
_SEED_SET_CURATED_YAMLS = (
    "bulla/compositions/mcp_filesystem_git.yaml",
    "bulla/compositions/financial_pipeline.yaml",
    "bulla/compositions/mcp_fetch_filesystem_git.yaml",
    "bulla/compositions/auth_pipeline.yaml",
    "bulla/compositions/regime_break_dfd_violation.yaml",
    "bulla/compositions/regime_break_bridge_topology.yaml",
)


def _seed_set_load_registry_manifests(manifests_dir: Path) -> dict[str, list[dict]]:
    """Inlined version of the Sprint 4 helper. Avoids importing
    `sprint4_canonical_pair` (which has a heavy script body with
    side-effects at import time)."""
    manifests: dict[str, list[dict]] = {}
    if not manifests_dir.is_dir():
        return manifests
    for f in sorted(manifests_dir.glob("*.json")):
        if f.name == "coherence.db" or f.stem.startswith("."):
            continue
        try:
            data = json.loads(f.read_text())
            tools = data.get("tools", [])
            if tools:
                manifests[f.stem] = tools
        except (json.JSONDecodeError, KeyError):
            continue
    return manifests


def _seed_set_build_pair(
    server_a: str, tools_a: list[dict],
    server_b: str, tools_b: list[dict],
) -> Composition:
    """Inlined Sprint 4 pair-composition builder. Same prefix convention
    as `BullaGuard.from_tools_list` so the composition has multi-server
    `xxx__tool` names."""
    from bulla.guard import BullaGuard
    prefixed: list[dict] = []
    for t in tools_a:
        p = dict(t)
        p["name"] = f"{server_a}__{t['name']}"
        prefixed.append(p)
    for t in tools_b:
        p = dict(t)
        p["name"] = f"{server_b}__{t['name']}"
        prefixed.append(p)
    return BullaGuard.from_tools_list(
        prefixed, name=f"{server_a}+{server_b}"
    ).composition


def _seed_set_compositions(repo_root: Path) -> list[tuple[Composition, str]]:
    """Return (Composition, source_path) tuples for the Sprint 13 seed
    set: 10 compositions covering the regime lattice. Reuses existing
    Sprint 6 cycle family + (inlined Sprint 4) registry pair construction +
    curated YAMLs + Sprint 10 negative-control fixture."""
    out: list[tuple[Composition, str]] = []

    # Accept both the res-agentica monorepo root and the standalone Bulla root.
    # The earlier implementation silently produced a smaller seed set in the
    # standalone mirror because it always assumed a nested ``bulla/`` directory.
    bulla_root = repo_root / "bulla" if (repo_root / "bulla").is_dir() else repo_root

    # Registry pairs (inlined Sprint 4 helpers; no module-import side effects)
    manifests_dir = (
        bulla_root / "calibration" / "data" / "registry" / "manifests"
    )
    manifests = _seed_set_load_registry_manifests(manifests_dir)
    for a, b in _SEED_SET_REGISTRY_PAIRS:
        if a in manifests and b in manifests:
            try:
                comp = _seed_set_build_pair(a, manifests[a], b, manifests[b])
                out.append((comp, f"<registry pair: {a}+{b}>"))
            except Exception as e:
                print(f"warning: skipping pair {a}+{b}: {e}", file=sys.stderr)

    # Curated YAMLs
    for rel in _SEED_SET_CURATED_YAMLS:
        p = repo_root / rel
        if not p.exists() and rel.startswith("bulla/"):
            p = bulla_root / rel.removeprefix("bulla/")
        if p.exists():
            try:
                comp = load_composition(p)
                out.append((comp, str(p)))
            except Exception as e:
                print(f"warning: skipping {rel}: {e}", file=sys.stderr)

    # Cycle family A_{3,4} (synthetic, all-hidden exact-conservative)
    from bulla.model import Edge, SemanticDimension, ToolSpec
    n = 3 * 4
    tools = tuple(
        ToolSpec(name=f"t{i}", internal_state=("f",), observable_schema=())
        for i in range(n)
    )
    edges = []
    for c in range(3):
        for i in range(4):
            u = c * 4 + i
            v = c * 4 + (i + 1) % 4
            edges.append(Edge(
                from_tool=f"t{u}", to_tool=f"t{v}",
                dimensions=(SemanticDimension(name="f_match", from_field="f", to_field="f"),),
            ))
    out.append((
        Composition(name="A_3_4", tools=tools, edges=tuple(edges)),
        "<cycle family k=3 m=4>",
    ))

    # Negative control: malformed_non_projective via Python construction
    # (parser blocks the YAML version, so we build the same shape via the
    # model API to demonstrate violations propagation)
    t1 = ToolSpec(
        name="t1", internal_state=("hidden_a",), observable_schema=("secret",)
    )
    t2 = ToolSpec(
        name="t2", internal_state=("hidden_b",), observable_schema=("secret",)
    )
    edge = Edge(
        from_tool="t1", to_tool="t2",
        dimensions=(SemanticDimension(
            name="secret_match", from_field="secret", to_field="secret"
        ),),
    )
    out.append((
        Composition(name="malformed_non_projective_negative_control",
                    tools=(t1, t2), edges=(edge,)),
        "<negative control: parser-blocked YAML reproduced via Python API>",
    ))

    return out


def _format_certify_text(cert) -> str:
    """Human-readable v1.0 certificate summary.

    Sprint 14: reads from the `claims` block as the source of truth, with
    `display.fee_interpretation` and `display.repair_semantics` reproduced
    verbatim for back-compat. Regime fields shown for evidence."""
    r = cert.regime
    subject = cert.subject
    diagnostic = cert.diagnostic
    claims = cert.claims
    lines: list[str] = []
    lines.append("")
    lines.append(f"Composition: {subject['name']}")
    if subject.get("source_path"):
        lines.append(f"  source:                  {subject['source_path']}")
    lines.append(f"  composition_sha256:      {subject['composition_sha256'][:16]}…")
    lines.append(f"  certificate_content_hash: {cert.certificate_content_hash[:23]}…")
    lines.append(f"  schema_version:          {cert.certificate_schema_version}")
    lines.append("")
    lines.append("  REGIME (evidence):")
    lines.append(f"    is_well_formed_for_fee:        {r.is_well_formed_for_fee}")
    lines.append(f"    has_projective_observables:    {r.has_projective_observables}")
    lines.append(f"    has_dfd_conservative:          {r.has_dfd_conservative}")
    lines.append(f"    has_chp_conservative:          {r.has_chp_conservative}")
    lines.append(f"    is_exact_regime_conservative:  {r.is_exact_regime_conservative}")
    lines.append(f"    is_all_hidden:                 {r.is_all_hidden}")
    lines.append("")
    lines.append("  DIAGNOSTIC:")
    lines.append(f"    coherence_fee:           {diagnostic['coherence_fee']}")
    lines.append(f"    blind_spots:             {diagnostic['blind_spots_count']}")
    lines.append(f"    bridges_recommended:     {diagnostic['bridges_count']}")
    lines.append(f"    n_unbridged:             {diagnostic['n_unbridged']}")
    if diagnostic.get("cross_server_decomposition") is not None:
        d = diagnostic["cross_server_decomposition"]
        lines.append("")
        lines.append("  CROSS-SERVER:")
        lines.append(f"    n_servers:               {d['n_servers']}")
        lines.append(f"    servers:                 {d['servers']}")
        lines.append(f"    total_fee:               {d['total_fee']}")
        lines.append(f"    local_fees:              {d['local_fees']}")
        lines.append(f"    boundary_fee:            {d['boundary_fee']}")
    lines.append("")
    lines.append("  CLAIMS (machine-verifiable):")
    for claim_name in (
        "schema_shape_valid", "fee_is_nonnegative", "fee_is_interpretable",
        "exact_disclosure_equivalence", "repair_basis_status",
        "subject_bound",
    ):
        c = claims[claim_name]
        status_glyph = {
            "certified": "✓",
            "candidate": "?",
            "not_certified": "✗",
            "not_applicable": "—",
        }.get(c.status, " ")
        line = f"    {status_glyph} {claim_name:34s} status={c.status:14s} value={c.value}"
        lines.append(line)
        if c.licensed_by:
            lines.append(f"      licensed_by: {list(c.licensed_by)}")
        if c.not_licensed:
            lines.append(f"      not_licensed: {list(c.not_licensed)}")
    lines.append("")
    comp = cert.display["completeness"]
    _verdict_glyph = {
        "proven": "✓ PROVEN",
        "lower_bound": "~ LOWER BOUND",
        "not_applicable": "– N/A",
    }
    lines.append(f"  COMPLETENESS: {_verdict_glyph.get(comp['verdict'], comp['verdict'])}")
    lines.append(f"    {comp['interpretation']}")
    for rider in comp["scope"]:
        lines.append(f"    · {rider}")
    lines.append("")
    lines.append("  DISPLAY (v0 free-text labels; UI back-compat — do NOT parse):")
    lines.append(f"    fee_interpretation:      {cert.display['fee_interpretation']!r}")
    lines.append(f"    repair_semantics:        {cert.display['repair_semantics']}")
    if cert.violations:
        lines.append("")
        lines.append("  ⚠ SCHEMA-SHAPE VIOLATIONS:")
        for v in cert.violations:
            lines.append(f"    - tool `{v.tool_name}`: fields {list(v.fields)} "
                         f"(kind: {v.kind})")
    return "\n".join(lines) + "\n"


def _cmd_certify(args: argparse.Namespace) -> None:
    """Sprint 13: emit per-composition certificate(s). Bundles regime +
    diagnostic + cross-server + witness geometry + interpretation labels
    into one structured artifact per composition."""
    _configure_packs_from_args(args)
    fmt = getattr(args, "format", "text")

    # Repo root for seed-set construction. Cwd is presumed to be the
    # repo root or a worktree where `bulla/` lives.
    repo_root = Path.cwd()
    while not (repo_root / "bulla").is_dir():
        if repo_root.parent == repo_root:
            repo_root = Path.cwd()  # fallback to cwd
            break
        repo_root = repo_root.parent

    # Resolve compositions to certify.
    pairs: list[tuple[Composition, str]] = []
    if getattr(args, "seed_set", False):
        pairs = _seed_set_compositions(repo_root)
        if not pairs:
            print("Error: seed set is empty (no fixtures found)", file=sys.stderr)
            sys.exit(1)
    else:
        if not args.files:
            print(
                "Error: provide composition files or directories, or use --seed-set",
                file=sys.stderr,
            )
            sys.exit(1)
        paths = _resolve_paths(args.files)
        if not paths:
            print("No composition files found.", file=sys.stderr)
            sys.exit(1)
        for path in paths:
            try:
                comp = _load_with_regime_warning(path)
                pairs.append((comp, str(path)))
            except CompositionError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
            except Exception as e:
                print(f"Error processing {path}: {e}", file=sys.stderr)
                sys.exit(1)

    # Build certificates
    certs = [
        certify(comp, source_path=src) for comp, src in pairs
    ]

    # Optionally sign each certificate under an agent identity (bulla[identity]).
    # Signing is creation-time: it sets the issuer (committed in the content hash)
    # and a detached ed25519 signature. Bulla signs, never mints.
    if getattr(args, "sign", False):
        from bulla.certificate import sign_certificate

        signer = _load_signer_or_exit(
            getattr(args, "key", None), getattr(args, "issuer", None)
        )
        certs = [sign_certificate(c, signer) for c in certs]

    # Optional output file
    output = getattr(args, "output", None)

    if fmt == "json":
        if len(certs) == 1:
            payload = to_dict(certs[0])
        else:
            payload = [to_dict(c) for c in certs]
        text = json.dumps(payload, indent=2)
        if output:
            Path(output).write_text(text + "\n")
            print(f"  Wrote {len(certs)} certificate(s) to {output}",
                  file=sys.stderr)
        else:
            print(text)
        return

    # Text format
    sep = "─" * 60
    out_lines: list[str] = []
    for i, cert in enumerate(certs):
        if i > 0:
            out_lines.append(sep)
        out_lines.append(_format_certify_text(cert))
    text = "\n".join(out_lines)
    if output:
        Path(output).write_text(text)
        print(f"  Wrote {len(certs)} certificate(s) to {output}",
              file=sys.stderr)
    else:
        print(text, end="")


# ── identity: sign / verify / anchor (bulla[identity], bulla[ots]) ──────────
#
# Bulla SIGNS coherence attestations under an identity the agent already holds
# (default: a self-certifying did:key); it never mints identity. The signed
# certificate is the *deed*; anchoring it to the timechain (a public registry)
# is what makes the deed non-repudiable across time.

def _default_key_path() -> Path:
    return Path.home() / ".bulla" / "identity.json"


def _load_signer_or_exit(key_path, issuer):
    """Load a LocalEd25519Signer from a key file (or the default), applying an
    optional external issuer override. Exits with a clear message on failure."""
    try:
        from bulla.identity import LocalEd25519Signer
    except ImportError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    path = Path(key_path) if key_path else _default_key_path()
    if not path.exists():
        print(
            f"Error: no signing key at {path}.\n"
            f"  Run `bulla key gen` first, or pass --key FILE.",
            file=sys.stderr,
        )
        sys.exit(1)
    signer = LocalEd25519Signer.from_keyfile_dict(json.loads(path.read_text()))
    if issuer and issuer != signer.issuer:
        signer = LocalEd25519Signer(seed=signer.seed, issuer_override=issuer)
    return signer


def _cmd_key(args: argparse.Namespace) -> None:
    """Bare `bulla key` → show help for the key subcommands."""
    print(
        "usage: bulla key gen [-o FILE] [--force]\n\n"
        "Generate the local ed25519 signing identity (a did:key). Bulla signs\n"
        "coherence attestations under it; it never issues identities.",
        file=sys.stderr,
    )
    sys.exit(2)


def _cmd_key_gen(args: argparse.Namespace) -> None:
    try:
        from bulla.identity import LocalEd25519Signer
    except ImportError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    path = Path(args.output) if getattr(args, "output", None) else _default_key_path()
    if path.exists() and not getattr(args, "force", False):
        print(
            f"Error: {path} already exists. Use --force to overwrite, or -o to pick another path.",
            file=sys.stderr,
        )
        sys.exit(1)
    signer = LocalEd25519Signer.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(signer.to_keyfile_dict(), indent=2) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    print(
        f"Generated ed25519 identity:\n"
        f"  did   {signer.verification_method}\n"
        f"  key   {path}  (contains the secret key — keep it safe)",
        file=sys.stderr,
    )


def _cmd_verify(args: argparse.Namespace) -> None:
    """Verify a certificate: integrity (hash, always), authenticity (signature),
    and anchor (a `.ots` sidecar, if present)."""
    from bulla.certificate import verify_certificate_integrity

    cert = json.loads(Path(args.certificate).read_text())
    integrity = verify_certificate_integrity(cert)

    authenticity = None
    sig = cert.get("signature")
    if sig:
        from bulla.identity import verify_proof

        pub = None
        if getattr(args, "key", None):
            import base64

            kd = json.loads(Path(args.key).read_text())
            if isinstance(kd, dict) and kd.get("public_key_b64"):
                pub = base64.b64decode(kd["public_key_b64"])
        res = verify_proof(cert.get("certificate_content_hash", ""), sig, public_key=pub)
        authenticity = {
            "authentic": res.authentic,
            "method": res.method,
            "issuer": res.issuer,
            "detail": res.detail,
        }

    anchor = None
    ots_path = Path(str(args.certificate) + ".ots")
    if ots_path.exists():
        try:
            from bulla.ots import verify_certificate_anchor

            anchor = verify_certificate_anchor(cert, ots_path.read_text().strip())
        except ImportError:
            anchor = {"valid": False, "error": "install bulla[ots] to verify anchors"}

    # inclusion: the omission-closer (rung 4). Demand the deed be logged in the
    # registry YOU name (local path or a remote read-only URL). Refuse the unlogged.
    included = None
    root_trust = None
    root_ok = False
    if getattr(args, "registry", None):
        from bulla.registry import classify_root_trust, deed_leaf, verify_inclusion_record

        reg = _open_registry(args.registry)
        att = cert.get("attestation_hash")
        trusted_root = getattr(args, "trusted_root", None)
        root_ots = None
        if getattr(args, "root_ots", None):
            p = Path(args.root_ots)
            root_ots = p.read_text().strip() if p.exists() else str(args.root_ots)
        # bind the inclusion proof to THIS cert's leaf (else a host can borrow a valid
        # proof for an unrelated leaf under the same root)
        expected_leaf = deed_leaf({
            "issuer": (cert.get("issuer") or {}).get("id"),
            "content_hash": cert.get("certificate_content_hash"),
            "attestation_hash": att,
        }) if att and cert.get("certificate_content_hash") else None
        try:  # fail closed: an unreachable registry cannot confirm inclusion
            proof = reg.inclusion_by_attestation(att) if att else None
            served_root = proof.get("root") if proof else None
            included = bool(proof) and verify_inclusion_record(
                proof, expected_leaf=expected_leaf)
            root_trust, root_ok = classify_root_trust(
                getattr(reg, "is_remote", False), served_root, trusted_root, root_ots)
        except Exception as e:
            print(f"inclusion     UNREACHABLE — {e} (refusing)", file=sys.stderr)
            included, root_trust, root_ok = False, "unreachable", False

    result = {"integrity": integrity, "authenticity": authenticity, "anchor": anchor}
    if included is not None:
        result["included"] = included
        result["root_trust"] = root_trust

    if getattr(args, "format", "text") == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"integrity     {'OK — content hash matches' if integrity else 'FAILED — tampered'}")
        if authenticity is None:
            print("authenticity  unsigned (content-hash only)")
        else:
            verdict = "OK" if authenticity["authentic"] else "FAILED"
            extra = f"  ({authenticity['detail']})" if authenticity["detail"] else ""
            print(
                f"authenticity  {verdict} via {authenticity['method']}  "
                f"issuer={authenticity['issuer']}{extra}"
            )
        if anchor is None:
            print("anchor        none — run `bulla anchor` to record the deed publicly")
        elif anchor.get("valid"):
            print(f"anchor        {anchor.get('status')}")
        else:
            print(f"anchor        invalid — {anchor.get('error')}")
        if included is not None:
            if not included:
                print("inclusion     ABSENT — refuse the unlogged")
            elif root_trust == "mismatch":
                print("inclusion     ROOT MISMATCH — host served a different root "
                      "than you pinned (refuse)")
            elif root_ok:
                print(f"inclusion     OK — logged against a root you trust ({root_trust})")
            else:
                print(f"inclusion     {root_trust.upper().replace('-', ' ')} — not "
                      "independently trusted; pin the root (--trusted-root/--root-ots) "
                      "to proceed, else you trust the operator")

    ok = integrity and (authenticity is None or authenticity["authentic"])
    if included is not None:
        ok = ok and included and root_ok  # proceed requires an independently trusted root
    sys.exit(0 if ok else 1)


def _cmd_gate(args: argparse.Namespace) -> None:
    """The recourse GATE — the OBSERVE -> ENFORCE move. Where `bulla verify` REPORTS the
    checks, `gate` ENFORCES a relying-party policy: proceed only if the counterparty's
    deed is authentic AND included under a root you trust independently of the host AND
    certifies coherence_fee <= --require-fee. On refuse it emits a contestable refusal
    certificate naming the deficiency and the cure. Exit 0 = PROCEED, 1 = REFUSE.

    Parameterized entirely by flags — point it at YOUR registry + composition; nothing is
    hardcoded. This is the neutrality bar made executable (a relying party need not route
    through an interested party)."""
    from bulla.recourse_gate import build_refusal_certificate, evaluate_gate, GatePolicy

    def _load_json(path: str, label: str) -> dict:
        try:
            return json.loads(Path(path).read_text())
        except FileNotFoundError:
            print(f"Error: {label} file not found: {path}", file=sys.stderr)
            sys.exit(2)
        except json.JSONDecodeError as e:
            print(f"Error: {label} is not valid JSON ({path}): {e}\n"
                  f"  Tip: emit a JSON certificate with "
                  f"`bulla certify --sign <comp>.yaml --key <key> --output <cert>.json --format json`.",
                  file=sys.stderr)
            sys.exit(2)

    cert = _load_json(args.certificate, "--certificate") if getattr(args, "certificate", None) else None
    deed = _load_json(args.deed, "--deed") if getattr(args, "deed", None) else {}
    if cert and not deed:  # derive the deed triple from the certificate
        deed = {
            "issuer": (cert.get("issuer") or {}).get("id"),
            "content_hash": cert.get("certificate_content_hash"),
            "attestation_hash": cert.get("attestation_hash"),
            "composition_hash": (cert.get("subject") or {}).get("composition_sha256"),
        }
    att = deed.get("attestation_hash") or (cert or {}).get("attestation_hash")
    if not att:
        print("Error: pass --certificate or --deed (an attestation_hash is required).", file=sys.stderr)
        sys.exit(2)

    reg = _open_registry(args.registry)
    if reg is None:
        print("Error: `bulla gate` needs --registry (a local path or an http(s) URL).", file=sys.stderr)
        sys.exit(2)

    root_ots = None
    if getattr(args, "root_ots", None):
        p = Path(args.root_ots)
        root_ots = p.read_text().strip() if p.exists() else str(args.root_ots)
    signer = _load_signer_or_exit(args.key, getattr(args, "issuer", None)) if getattr(args, "key", None) else None

    # Fee-gating is explicit opt-in (--require-fee N): the fee is a
    # disclosure/omission signal, not an execution predictor (FALSIFICATIONS.md).
    policy = GatePolicy(
        max_fee=getattr(args, "require_fee", None),
        expected_composition_hash=getattr(args, "composition_hash", None),
    )
    try:  # fail closed: an unreachable registry cannot confirm inclusion
        proof = reg.inclusion_by_attestation(att)
    except Exception as e:
        print(f"gate          REFUSE — could not reach the registry ({e})", file=sys.stderr)
        sys.exit(1)

    decision = evaluate_gate(
        deed_rec=deed, inclusion_rec=proof, certificate=cert,
        trusted_root=getattr(args, "trusted_root", None), root_ots=root_ots,
        is_remote=getattr(reg, "is_remote", False), policy=policy)

    out = {
        "disposition": decision.disposition,
        "deficiency": decision.deficiency,
        "root_trust": decision.root_trust,
        "fee": decision.fee,
        "included": decision.included,
        "reason": decision.reason,
    }
    if not decision.proceed:
        out["refusal_certificate"] = build_refusal_certificate(
            decision, subject_deed=deed,
            disclose=tuple(getattr(args, "disclose", None) or ()), signer=signer)

    output_format = getattr(args, "format", "text")
    if output_format == "json":
        print(json.dumps(out, indent=2))
    elif output_format == "brief":
        included = str(bool(decision.included)).lower()
        if decision.proceed:
            print(
                f"PROCEED  included={included} · root_trust={decision.root_trust}"
            )
        else:
            print(
                f"REFUSE   {decision.deficiency} · included={included} · "
                f"root_trust={decision.root_trust}"
            )
            cure = (out.get("refusal_certificate") or {}).get("cure") or {}
            if decision.deficiency == "UNPINNED_ROOT":
                print("CURE     present the deed under an independently trusted root")
            elif cure.get("human"):
                print(f"CURE     {cure['human']}")
    elif decision.proceed:
        print(f"gate          PROCEED — {decision.reason}")
    else:
        print(f"gate          REFUSE [{decision.deficiency}] — {decision.reason}")
        cure = (out.get("refusal_certificate") or {}).get("cure") or {}
        if cure.get("human"):
            print(f"cure          {cure['human']}")
    sys.exit(0 if decision.proceed else 1)


def _cmd_anchor(args: argparse.Namespace) -> None:
    """Anchor a signed certificate's attestation hash to the Bitcoin timechain,
    writing a `<cert>.ots` sidecar. The anchor is the public registry record that
    makes the signed deed non-repudiable across time."""
    cert = json.loads(Path(args.certificate).read_text())
    if not cert.get("signature"):
        print(
            "Error: certificate is unsigned. Sign it first: `bulla certify --sign`.",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        from bulla.ots import anchor_certificate
    except ImportError:
        print(
            "Error: anchoring requires the [ots] extra: pip install bulla[ots]",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        proof_b64 = anchor_certificate(cert)
    except Exception as e:
        print(f"Error anchoring: {e}", file=sys.stderr)
        sys.exit(1)
    ots_path = Path(str(args.certificate) + ".ots")
    ots_path.write_text(proof_b64 + "\n")
    print(
        f"Anchored {args.certificate} → {ots_path}\n"
        f"  attestation_hash {cert.get('attestation_hash')}\n"
        f"  submitted to the Bitcoin timechain (pending; re-verify with `bulla verify` in ~2h).",
        file=sys.stderr,
    )


# ── registry: the append-only deed log ──────────────────────────────────────

def _default_registry_path() -> Path:
    return Path.home() / ".bulla" / "registry.jsonl"


def _open_deed_log(args):
    from bulla.registry import DeedLog

    log_path = getattr(args, "log", None)
    return DeedLog(Path(log_path) if log_path else _default_registry_path())


def _cmd_registry(args: argparse.Namespace) -> None:
    print(
        "usage: bulla registry {append,log,prove,root,anchor,serve} …\n\n"
        "The append-only deed log: any party can relay a certificate its issuer\n"
        "signed; once logged, a deed cannot be deleted or reordered, and the full\n"
        "logged set under an issuer is enumerable. Closes deletion; makes omission\n"
        "checkable (a relying party demands an inclusion proof) — it does not compel\n"
        "an agent to log a deed, and does not resist rekey.",
        file=sys.stderr,
    )
    sys.exit(2)


def _cmd_registry_append(args: argparse.Namespace) -> None:
    cert = json.loads(Path(args.certificate).read_text())
    log = _open_deed_log(args)
    try:
        idx = log.append_certificate(cert)  # the verified submission boundary
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(
        f"Appended deed at index {idx}\n"
        f"  issuer  {(cert.get('issuer') or {}).get('id')}\n"
        f"  deed    {cert.get('attestation_hash')}\n"
        f"  root    {log.root()}  (anchor it: `bulla registry root` then anchor)",
        file=sys.stderr,
    )
    print(idx)


def _cmd_registry_log(args: argparse.Namespace) -> None:
    log = _open_deed_log(args)
    issuer = getattr(args, "issuer", None)
    rows = log.deeds(issuer)
    if getattr(args, "format", "text") == "json":
        print(json.dumps({
            "tree_size": len(log),
            "root": log.root(),
            "deeds": [
                {"index": i, "issuer": d.issuer, "content_hash": d.content_hash,
                 "attestation_hash": d.attestation_hash, "signature": d.signature}
                for i, d in rows
            ],
        }, indent=2))
        return
    print(f"deed log: {len(log)} deed(s)  root {log.root()}")
    if issuer:
        print(f"issuer {issuer}: {len(rows)} deed(s)")
    for i, d in rows:
        print(f"  [{i}] {d.issuer}  {d.attestation_hash}")


def _cmd_registry_prove(args: argparse.Namespace) -> None:
    log = _open_deed_log(args)
    if not 0 <= args.index < len(log):
        print(
            f"Error: index {args.index} out of range (the log has {len(log)} deed(s))",
            file=sys.stderr,
        )
        sys.exit(1)
    print(json.dumps(log.inclusion(args.index), indent=2))


def _cmd_registry_root(args: argparse.Namespace) -> None:
    print(_open_deed_log(args).root())


def _cmd_registry_anchor(args: argparse.Namespace) -> None:
    import base64

    log = _open_deed_log(args)
    if len(log) == 0:
        print("Error: the log is empty; nothing to anchor.", file=sys.stderr)
        sys.exit(1)
    try:
        from bulla.ots import stamp_hash
    except ImportError:
        print(
            "Error: anchoring requires the [ots] extra: pip install bulla[ots]",
            file=sys.stderr,
        )
        sys.exit(1)
    root = log.root()
    hex_root = root.split(":", 1)[1]
    try:
        proof = stamp_hash(hex_root)
    except Exception as e:
        print(f"Error anchoring: {e}", file=sys.stderr)
        sys.exit(1)
    out = Path(f"{log.path}.root.{hex_root[:12]}.ots")
    out.write_text(base64.b64encode(proof).decode("ascii") + "\n")
    print(
        f"Anchored registry root {root} → {out}\n"
        f"  tree_size {len(log)} (a checkpoint; re-anchor as the log grows)",
        file=sys.stderr,
    )


def _cmd_registry_serve(args: argparse.Namespace) -> None:
    """Serve the deed log read-only over HTTP — the online surface. A relying party
    on another machine can demand inclusion and look up deeds-by-composition. The
    proof verifies against the root THIS host returns, so a remote verifier must pin
    that root (an OTS anchor, or a value obtained out of band) to trust it — see
    `bulla verify --trusted-root/--root-ots`. Absent a pin it is trusting the operator."""
    log = _open_deed_log(args)
    from bulla.http_registry import make_server

    srv = make_server(log, host=args.host, port=args.port)
    host, port = srv.server_address
    print(
        f"bulla registry serving {log.path} read-only at http://{host}:{port}\n"
        f"  tree_size {len(log)}  root {log.root()}\n"
        f"  GET /root · /inclusion?attestation=<id> · /by-composition?composition=<hash>\n"
        f"  (remote verifiers must pin this root — `bulla verify --trusted-root <hash>` —"
        f" or they are trusting this operator)",
        file=sys.stderr,
    )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


def _cmd_certify_update(args: argparse.Namespace) -> None:
    """G26: semantic update certification for old/new compositions."""
    _configure_packs_from_args(args)
    try:
        old_comp = _load_with_regime_warning(args.old_file)
        new_comp = _load_with_regime_warning(args.new_file)
    except CompositionError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    from bulla.compute.semver import assess_update

    assessment = assess_update(old_comp, new_comp)
    if args.format == "json":
        print(json.dumps(assessment.to_dict(), indent=2))
        return

    print("Semantic update assessment")
    print(f"  old_fee:               {assessment.old_fee}")
    print(f"  new_fee:               {assessment.new_fee}")
    print(f"  delta_r:               {assessment.delta_r}")
    print(f"  coherence_preserving:  {assessment.coherence_preserving}")
    print(f"  update_kind:           {assessment.update_kind}")
    print(f"  minimum_bridge_delta:  {assessment.minimum_bridge_delta}")


def _cmd_diagnose(args: argparse.Namespace) -> None:
    _configure_packs_from_args(args)
    if args.examples:
        paths = _resolve_paths([_examples_dir()])
    elif not args.files:
        print("Error: provide composition files or use --examples",
              file=sys.stderr)
        sys.exit(1)
    else:
        paths = _resolve_paths(args.files)

    if not paths:
        print("No composition files found.", file=sys.stderr)
        sys.exit(1)

    witness = getattr(args, "witness", False)
    # Sprint 11 Phase 5: track (diag, path, comp) so format_json can
    # emit the regime block under --format json without re-parsing.
    diagnostics: list[tuple] = []  # list[tuple[Diagnostic, Path, Composition]]
    for path in paths:
        try:
            # Sprint 11 Phase 2: centralized helper emits regime warnings
            # to stderr; commands no longer have to repeat the boilerplate.
            comp = _load_with_regime_warning(path)
            diag = diagnose(comp, include_witness_geometry=witness)
            diagnostics.append((diag, path, comp))
        except CompositionError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error processing {path}: {e}", file=sys.stderr)
            sys.exit(1)

    fmt = getattr(args, "format", "text")
    brief = getattr(args, "brief", False)

    if fmt == "sarif":
        # SARIF formatter takes (diag, path) tuples.
        print(format_sarif([(d, p) for d, p, _ in diagnostics]))
    elif fmt == "json":
        # Sprint 12: regime block is opt-in via --regime to preserve
        # byte-identity with the 0.34.0 golden JSON fixture.
        include_regime = getattr(args, "regime", False)
        if len(diagnostics) == 1:
            d0, p0, c0 = diagnostics[0]
            print(format_json(d0, p0, comp=c0 if include_regime else None))
        else:
            combined = [
                json.loads(format_json(d, p, comp=c if include_regime else None))
                for d, p, c in diagnostics
            ]
            print(json.dumps(combined, indent=2))
    elif brief:
        for diag, path, _comp in diagnostics:
            bs = len(diag.blind_spots)
            status = "PASS" if diag.coherence_fee == 0 else "FAIL"
            print(
                f"  {status}  {path.name}  "
                f"blind_spots={bs}  fee={diag.coherence_fee}"
            )
        print()
    else:
        sep = "\u2500" * 60
        for i, (d, _p, _c) in enumerate(diagnostics):
            if i > 0:
                print(sep)
            print(format_text(d))

        if len(diagnostics) > 1:
            print("\u2501" * 60)
            fees = [d.coherence_fee for d, _, _ in diagnostics]
            total_bs = sum(len(d.blind_spots) for d, _, _ in diagnostics)
            print(f"  Summary: {len(diagnostics)} compositions")
            print(
                f"  Fully bridged (fee = 0): "
                f"{sum(1 for f in fees if f == 0)}"
            )
            print(
                f"  Require bridging (fee > 0): "
                f"{sum(1 for f in fees if f > 0)}"
            )
            print(f"  Total blind-spot dimensions: {total_bs}")
            print(f"  Max coherence fee: {max(fees)}")
            print()


def _cmd_check(args: argparse.Namespace) -> None:
    _configure_packs_from_args(args)
    if args.examples:
        paths = _resolve_paths([_examples_dir()])
    elif not args.files:
        print("Error: provide composition files or use --examples",
              file=sys.stderr)
        sys.exit(1)
    else:
        paths = _resolve_paths(args.files)

    if not paths:
        print("No composition files found.", file=sys.stderr)
        sys.exit(1)

    witness = getattr(args, "witness", False)
    diagnostics: list[tuple] = []
    for path in paths:
        try:
            # Sprint 11 Phase 2: centralized helper.
            comp = _load_with_regime_warning(path)
            diag = diagnose(comp, include_witness_geometry=witness)
            diagnostics.append((diag, path))
        except CompositionError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error processing {path}: {e}", file=sys.stderr)
            sys.exit(1)

    failed = False
    max_fee = getattr(args, "max_fee", None)
    for diag, path in diagnostics:
        bs_count = len(diag.blind_spots)
        ub_count = diag.n_unbridged
        if bs_count > args.max_blind_spots:
            failed = True
            print(
                f"FAIL {path.name}: {bs_count} blind spot(s) "
                f"(max allowed: {args.max_blind_spots})",
                file=sys.stderr,
            )
        if ub_count > args.max_unbridged:
            failed = True
            print(
                f"FAIL {path.name}: {ub_count} unbridged edge(s) "
                f"(max allowed: {args.max_unbridged})",
                file=sys.stderr,
            )
        if max_fee is not None and diag.coherence_fee > max_fee:
            failed = True
            print(
                f"FAIL {path.name}: fee {diag.coherence_fee} "
                f"(max allowed: {max_fee})",
                file=sys.stderr,
            )

    # ── Baseline staleness + regression check ───────────────────────
    baseline_path = getattr(args, "baseline", None)
    baseline_diff = None
    if baseline_path is not None:
        from bulla.lifecycle import diff_receipts, receipt_from_dict
        from bulla.witness import witness as _witness

        if not baseline_path.exists():
            print(f"Error: baseline receipt not found: {baseline_path}",
                  file=sys.stderr)
            sys.exit(1)

        # Baseline mode requires exactly one composition
        if len(diagnostics) != 1:
            print(
                f"Error: --baseline requires exactly one composition file, "
                f"got {len(diagnostics)}",
                file=sys.stderr,
            )
            sys.exit(1)

        with open(baseline_path) as f:
            baseline_data = json.load(f)

        diag_current, path_current = diagnostics[0]
        comp_current = load_composition(path_current)
        current_receipt = _witness(diag_current, comp_current)
        baseline_receipt = receipt_from_dict(baseline_data)

        baseline_diff = diff_receipts(baseline_receipt, current_receipt)
        if baseline_diff.should_fail_gate:
            failed = True

    # ── Output (after all checks, including baseline) ─────────────
    fmt = getattr(args, "format", "text")
    if fmt == "sarif":
        print(format_sarif(diagnostics))
    elif fmt == "json":
        combined = [json.loads(format_json(d, p)) for d, p in diagnostics]
        result: dict = {
            "passed": not failed,
            "max_blind_spots": args.max_blind_spots,
            "max_unbridged": args.max_unbridged,
            "compositions": combined,
        }
        if baseline_diff is not None:
            result["baseline"] = baseline_diff.to_dict()
        print(json.dumps(result, indent=2))
    else:
        for diag, path in diagnostics:
            bs = len(diag.blind_spots)
            ub = diag.n_unbridged
            status = "PASS" if (
                bs <= args.max_blind_spots and ub <= args.max_unbridged
            ) else "FAIL"
            print(
                f"  {status}  {path.name}  "
                f"blind_spots={bs}  unbridged={ub}  "
                f"fee={diag.coherence_fee}"
            )

        if baseline_diff is not None:
            print()
            if baseline_diff.is_stale:
                print(f"  STALE vs baseline: {baseline_diff.summary()}",
                      file=sys.stderr)
            if baseline_diff.is_regression:
                print(f"  REGRESSION vs baseline: {baseline_diff.summary()}",
                      file=sys.stderr)

        print()
        if failed:
            print("  Result: FAIL")
            print()
            print("  Run `bulla diagnose <file>` for full details.")
        else:
            print("  Result: PASS")
        print()

    # ── Certificate output (v0.38.0: unify check + certify primitives) ──
    # Writes BEFORE exit so certificates are produced regardless of the
    # CI gate verdict. Used by G24 self-host pipeline-CI to record state
    # at each commit hash in the historical analysis window.
    cert_out = getattr(args, "certificate_out", None)
    if cert_out is not None:
        cert_dicts = [
            to_dict(certify(_load_with_regime_warning(path),
                            source_path=str(path)))
            for _, path in diagnostics
        ]
        payload = cert_dicts[0] if len(cert_dicts) == 1 else cert_dicts
        cert_out.write_text(json.dumps(payload, indent=2) + "\n")
        print(
            f"  Wrote {len(cert_dicts)} certificate(s) to {cert_out}",
            file=sys.stderr,
        )

    sys.exit(1 if failed else 0)


def _cmd_diff(args: argparse.Namespace) -> None:
    """Compare two receipt JSON files and show what changed."""
    from bulla.lifecycle import diff_receipts, receipt_from_dict

    for label, path in [("baseline", args.baseline), ("current", args.current)]:
        if not path.exists():
            print(f"Error: {label} receipt not found: {path}", file=sys.stderr)
            sys.exit(1)

    with open(args.baseline) as f:
        baseline_data = json.load(f)
    with open(args.current) as f:
        current_data = json.load(f)

    baseline = receipt_from_dict(baseline_data)
    current = receipt_from_dict(current_data)
    diff = diff_receipts(baseline, current)

    fmt = getattr(args, "format", "text")
    if fmt == "json":
        print(json.dumps(diff.to_dict(), indent=2))
    else:
        print(f"  Comparison: {args.baseline.name} → {args.current.name}")
        print(f"  Summary:    {diff.summary()}")
        print()
        if diff.composition_changed:
            print(f"  Composition: CHANGED (baseline stale)")
        else:
            print(f"  Composition: unchanged")
        print(f"  Fee:         {baseline.fee} → {current.fee} (Δ{diff.fee_delta:+d})")
        print(f"  Disposition: {baseline.disposition.value} → {current.disposition.value}")
        print(f"  Blind spots: {baseline.blind_spots_count} → {current.blind_spots_count} (Δ{diff.blind_spots_delta:+d})")
        if diff.contradiction_delta != 0:
            print(f"  Contradictions: Δ{diff.contradiction_delta:+d}")
        if diff.new_blind_spot_dimensions:
            print(f"  New dimensions: {', '.join(diff.new_blind_spot_dimensions)}")
        if diff.resolved_blind_spot_dimensions:
            print(f"  Resolved:       {', '.join(diff.resolved_blind_spot_dimensions)}")
        print()
        if diff.is_stale and diff.is_regression:
            print(f"  Verdict: STALE + REGRESSION")
        elif diff.is_stale:
            print(f"  Verdict: STALE (composition changed, metrics not worse)")
        elif diff.is_regression:
            print(f"  Verdict: REGRESSION")
        else:
            print(f"  Verdict: OK")
        print()

    sys.exit(1 if diff.should_fail_gate else 0)


def _cmd_infer(args: argparse.Namespace) -> None:
    _configure_packs_from_args(args)
    from bulla.infer.mcp import infer_from_manifest

    if not args.manifest.exists():
        print(f"Error: manifest not found: {args.manifest}", file=sys.stderr)
        sys.exit(1)

    try:
        result = infer_from_manifest(args.manifest)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        args.output.write_text(result)
        print(f"Wrote proto-composition to {args.output}", file=sys.stderr)
    else:
        print(result)


def _cmd_init(args: argparse.Namespace) -> None:
    from bulla.init import run_init
    run_init(output=args.output)


def _cmd_manifest(args: argparse.Namespace) -> None:
    from bulla.manifest import (
        generate_manifest_from_json,
        generate_manifest_from_tools,
        validate_manifest,
    )

    if getattr(args, "examples", False):
        example_tools = [
            {
                "name": "invoice-parser",
                "description": "Parse invoices and extract financial data",
                "inputSchema": {
                    "properties": {
                        "document_path": {"type": "string"},
                    },
                },
                "outputSchema": {
                    "properties": {
                        "total_amount": {"type": "number"},
                        "due_date": {"type": "string"},
                    },
                },
            },
            {
                "name": "payment-processor",
                "description": "Process payments",
                "inputSchema": {
                    "properties": {
                        "amount": {"type": "number"},
                        "payment_date": {"type": "string"},
                        "currency": {"type": "string"},
                    },
                },
            },
        ]
        manifests = generate_manifest_from_tools(example_tools)
        for m in manifests:
            print(yaml.dump(m, default_flow_style=False, sort_keys=False))
            print("---")
        print(
            f"  Generated {len(manifests)} example manifest(s).",
            file=sys.stderr,
        )
        return

    if getattr(args, "publish", None):
        from bulla.ots import publish_manifest
        path = Path(args.publish)
        if not path.exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)
        try:
            publish_manifest(path)
            print(f"  PUBLISHED  {path}")
            print("  Commitment hash anchored to Bitcoin timechain via OpenTimestamps.", file=sys.stderr)
            print("  Proof status: pending (confirm after ~2 hours with --verify).", file=sys.stderr)
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        return

    if getattr(args, "verify", None):
        from bulla.ots import verify_manifest as ots_verify
        path = Path(args.verify)
        if not path.exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)
        try:
            if getattr(args, "upgrade", False):
                from bulla.ots import upgrade_proof
                result = upgrade_proof(path)
                if result.get("upgraded"):
                    print(f"  UPGRADED  {path}", file=sys.stderr)
            result = ots_verify(path)
            if result.get("valid"):
                status = result["status"]
                print(f"  {status.upper()}  {path}  hash={result['commitment_hash'][:16]}...")
                if status == "confirmed":
                    print(f"  Bitcoin block(s): {result['bitcoin_block_heights']}")
                elif status == "pending":
                    print(f"  {result['note']}", file=sys.stderr)
            else:
                print(f"  INVALID  {path}: {result['error']}", file=sys.stderr)
                sys.exit(1)
        except ImportError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        return

    if args.validate:
        path = Path(args.validate)
        if not path.exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)
        issues = validate_manifest(path)
        if not issues:
            print(f"  VALID  {path}")
        else:
            for issue in issues:
                level = "info" if issue.startswith("Info:") else "error"
                print(f"  [{level}] {issue}")
            errors = [i for i in issues if not i.startswith("Info:")]
            sys.exit(1 if errors else 0)
        return

    if args.from_json:
        path = Path(args.from_json)
        if not path.exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)
        manifests = generate_manifest_from_json(path)
    elif args.from_server:
        from bulla.scan import scan_mcp_server
        tools = scan_mcp_server(args.from_server)
        manifests = generate_manifest_from_tools(tools)
    else:
        print("Error: provide --from-json, --from-server, or --validate", file=sys.stderr)
        sys.exit(1)

    import tempfile

    all_valid = True
    for m in manifests:
        output_yaml = yaml.dump(m, default_flow_style=False, sort_keys=False)
        if args.output:
            out_path = Path(args.output)
            if len(manifests) > 1:
                stem = out_path.stem
                suffix = out_path.suffix or ".yaml"
                name = m["tool"]["name"].replace("-", "_").replace(" ", "_")
                out_path = out_path.parent / f"{stem}_{name}{suffix}"
            out_path.write_text(output_yaml)
            print(f"Wrote manifest to {out_path}", file=sys.stderr)
            issues = validate_manifest(out_path)
        else:
            print(output_yaml)
            print("---")
            # Validate via temp file
            with tempfile.NamedTemporaryFile(
                suffix=".yaml", mode="w", delete=False
            ) as tf:
                tf.write(output_yaml)
                tmp_path = Path(tf.name)
            try:
                issues = validate_manifest(tmp_path)
            finally:
                tmp_path.unlink()

        errors = [i for i in issues if not i.startswith("Info:")]
        if errors:
            all_valid = False
            tool_name = m.get("tool", {}).get("name", "unknown")
            for err in errors:
                print(f"  [error] {tool_name}: {err}", file=sys.stderr)

    if all_valid:
        print(
            f"  Generated {len(manifests)} manifest(s), all valid.",
            file=sys.stderr,
        )


def _cmd_translate(args: argparse.Namespace) -> None:
    """Runtime value translation (bulla.bridges) — different operation
    from the diagnostic ``bulla bridge`` YAML rewriter.

    Resolves a value across conventions on a single dimension. Returns
    JSON: ``{"value": "...", "evidence": {...}, "receipt_hash": "..."}``.
    Exits 1 with a structured error JSON when no translator covers the
    request.
    """
    from bulla.bridges import translate, TranslationUnavailable

    try:
        result = translate(
            args.dimension,
            value=args.value,
            to_convention=args.to,
            from_convention=args.from_,
        )
    except TranslationUnavailable as exc:
        err = {
            "error": "translation_unavailable",
            "dimension": exc.dimension,
            "from_convention": exc.from_convention,
            "to_convention": exc.to_convention,
            "suggestion": exc.suggestion,
            "license_required": exc.license_required,
        }
        print(json.dumps(err, indent=2))
        sys.exit(1)

    out = {
        "value": result.value,
        "evidence": result.evidence.to_dict(),
        "receipt_hash": result.receipt.receipt_hash,
    }
    print(json.dumps(out, indent=2))


def _cmd_bridge(args: argparse.Namespace) -> None:
    """Generate bridged composition YAML or JSON patches from a diagnosed composition."""
    paths = _resolve_paths(args.files)
    if not paths:
        print("No composition files found.", file=sys.stderr)
        sys.exit(1)

    for path in paths:
        try:
            # Sprint 11 Phase 2: centralized helper.
            comp = _load_with_regime_warning(path)
            diag = diagnose(comp)
        except CompositionError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error processing {path}: {e}", file=sys.stderr)
            sys.exit(1)

        if not diag.blind_spots:
            print(f"  {path.name}: no blind spots — already fully bridged.")
            continue

        fmt = getattr(args, "format", "yaml")
        if fmt == "json-patch":
            from bulla.witness import witness
            receipt = witness(diag, comp)
            patches = [p.to_bulla_patch() for p in receipt.patches]
            print(json.dumps(patches, indent=2))
        else:
            # Generate bridged YAML
            raw = yaml.safe_load(path.read_text())
            tools_section = raw.get("tools", {})
            for br in diag.bridges:
                for tool_name in br.add_to:
                    if tool_name in tools_section:
                        tool = tools_section[tool_name]
                        internal = tool.get("internal_state", [])
                        obs = tool.get("observable_schema", [])
                        if br.field not in internal:
                            internal.append(br.field)
                        if br.field not in obs:
                            obs.append(br.field)

            output_yaml = yaml.dump(raw, default_flow_style=False, sort_keys=False)
            if args.output:
                out_path = Path(args.output)
                out_path.write_text(output_yaml)
                print(f"  Wrote bridged composition to {out_path}", file=sys.stderr)

                # Verify the bridge worked
                bridged_comp = load_composition(out_path)
                bridged_diag = diagnose(bridged_comp)
                before = len(diag.blind_spots)
                after = len(bridged_diag.blind_spots)
                print(
                    f"  {path.name}: {before} → {after} blind spots",
                    file=sys.stderr,
                )
            else:
                print(output_yaml)


def _cmd_witness(args: argparse.Namespace) -> None:
    """Diagnose and emit a WitnessReceipt as JSON."""
    paths = _resolve_paths(args.files)
    if not paths:
        print("No composition files found.", file=sys.stderr)
        sys.exit(1)

    from bulla.witness import witness

    receipts = []
    for path in paths:
        try:
            # Sprint 11 Phase 2: centralized helper (regime warning fires
            # on hand-authored compositions that bypass BullaGuard but
            # somehow pass the parser).
            comp = _load_with_regime_warning(path)
            diag = diagnose(comp)
        except CompositionError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error processing {path}: {e}", file=sys.stderr)
            sys.exit(1)

        receipt = witness(diag, comp)
        receipts.append(receipt.to_dict())

    if len(receipts) == 1:
        print(json.dumps(receipts[0], indent=2))
    else:
        print(json.dumps(receipts, indent=2))


def _format_compose_prescriptive(
    diag,
    receipt,
    comp,
    path: Path,
) -> str:
    """Format a single composition's diagnostic + receipt in natural-language
    'developer-actionable' form: explain the obstruction, list the precise
    fields to expose, and point to the auto-bridge command.

    This is the Shannon-moment output: an engineer brings a composition,
    gets back exactly what to do next — no JSON parsing required.
    """
    lines: list[str] = []
    rule = "═" * 64
    soft = "─" * 64
    lines.append(rule)
    lines.append(f"  Bulla Compose Report — {path.name}")
    lines.append(rule)
    lines.append("")
    fee = diag.coherence_fee
    n_bs = len(diag.blind_spots)
    disposition = receipt.disposition.value if hasattr(receipt.disposition, "value") else str(receipt.disposition)
    if fee == 0:
        lines.append(f"  Witness rank (fee): 0  ✓ COMPOSITION IS COHERENT")
        lines.append("")
        lines.append("  No obstructions detected. This composition is structurally safe:")
        lines.append("  no hidden convention dimensions, no cross-server blind spots.")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"  Witness rank (fee): {fee}  ⚠ {disposition}")
    lines.append("")
    if n_bs == fee:
        bs_note = (
            f"  {n_bs} blind-spot dimension{'s' if n_bs != 1 else ''} forming "
            f"{fee} independent obstruction class{'es' if fee != 1 else ''}."
        )
    else:
        bs_note = (
            f"  {n_bs} blind-spot dimension{'s' if n_bs != 1 else ''} "
            f"collapse into {fee} independent obstruction class{'es' if fee != 1 else ''}."
        )
    lines.append(bs_note)
    lines.append("")

    patches = receipt.patches
    if patches:
        lines.append(f"  To make this composition safe, expose {len(patches)} field"
                     f"{'s' if len(patches) != 1 else ''}:")
        lines.append("")
        for i, p in enumerate(patches, 1):
            lines.append(f"    {i}. tool `{p.target_tool}`, field `{p.field}`")
            lines.append(f"       Action: add `{p.field}` to {p.target_tool}.observable_schema")
            lines.append(f"       Bridges blind spot on edge: {p.eliminates_blind_spot}")
            if p.expected_fee_delta < 0:
                lines.append(f"       Expected fee delta: {p.expected_fee_delta}")
            lines.append("")

        lines.append(soft)
        lines.append("  Apply all bridges automatically:")
        lines.append(f"    bulla bridge {path} --output {path.stem}_bridged.yaml")
        lines.append("")
        lines.append("  Or apply manually by editing the YAML — each `Action:` line")
        lines.append("  above tells you the precise change. After bridging, re-run:")
        lines.append(f"    bulla compose {path.stem}_bridged.yaml")
        lines.append("  and you should see `fee = 0`.")
        lines.append("")
    else:
        lines.append(f"  Fee = {fee} but no machine-actionable patches were generated.")
        lines.append("  This typically means the obstructions involve unknown dimensions")
        lines.append("  (run `bulla discover` or `bulla audit` for diagnosis).")
        lines.append("")

    return "\n".join(lines)


def _cmd_compose(args: argparse.Namespace) -> None:
    """Bulla compose: diagnose one or more compositions and emit a
    prescriptive (developer-actionable) report.

    Wraps `diagnose` + `witness` into a single command with output
    tailored for engineers: instead of JSON, prints a natural-language
    explanation of what to change and a copy-pasteable next command.

    Use `--format json` for the same structured WitnessReceipt that
    `bulla witness` emits.
    """
    from bulla.witness import witness as build_witness_receipt

    paths = _resolve_paths(args.files)
    if not paths:
        print("No composition files found.", file=sys.stderr)
        sys.exit(1)

    fmt = getattr(args, "format", "prescriptive")

    diagnostics_and_receipts = []
    for path in paths:
        try:
            comp = _load_with_regime_warning(path)
            diag = diagnose(comp)
        except CompositionError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error processing {path}: {e}", file=sys.stderr)
            sys.exit(1)

        receipt = build_witness_receipt(diag, comp)
        diagnostics_and_receipts.append((diag, receipt, comp, path))

    if fmt == "json":
        receipts = [r.to_dict() for _, r, _, _ in diagnostics_and_receipts]
        if len(receipts) == 1:
            print(json.dumps(receipts[0], indent=2))
        else:
            print(json.dumps(receipts, indent=2))
    else:
        # prescriptive (default)
        for i, (diag, receipt, comp, path) in enumerate(diagnostics_and_receipts):
            if i > 0:
                print()
            print(_format_compose_prescriptive(diag, receipt, comp, path))

        # Summary footer for multi-file invocations
        if len(diagnostics_and_receipts) > 1:
            fees = [d.coherence_fee for d, _, _, _ in diagnostics_and_receipts]
            coherent = sum(1 for f in fees if f == 0)
            obstructed = sum(1 for f in fees if f > 0)
            print("═" * 64)
            print(
                f"  Summary: {len(fees)} composition{'s' if len(fees) != 1 else ''} "
                f"— {coherent} coherent, {obstructed} requiring disclosure."
            )
            print("═" * 64)


def _load_manifest_dir(manifests_dir: Path) -> dict[str, list[dict]]:
    """Load {server_name: tools_list} from a manifest directory."""
    if not manifests_dir.exists():
        raise FileNotFoundError(f"manifest directory not found: {manifests_dir}")
    if not manifests_dir.is_dir():
        raise ValueError(f"manifest path is not a directory: {manifests_dir}")

    server_tools: dict[str, list[dict]] = {}
    for path in sorted(manifests_dir.glob("*.json")):
        data = json.loads(path.read_text())
        tools = data.get("tools", []) if isinstance(data, dict) else data
        if not isinstance(tools, list):
            raise ValueError(f"manifest {path} does not contain a tools list")
        server_tools[path.stem] = tools
    if not server_tools:
        raise ValueError(f"no manifest JSON files found in {manifests_dir}")
    return server_tools


def _proxy_record_to_dict(record: "ProxyCallRecord") -> dict[str, object]:
    d: dict[str, object] = {
        "call_id": record.call_id,
        "server": record.server,
        "tool": record.tool,
        "arguments": record.arguments,
        "result": record.result,
        "flows": [
            {
                "source_call_id": flow.source_call_id,
                "source_server": flow.source_server,
                "source_tool": flow.source_tool,
                "source_field": flow.source_field,
                "target_server": flow.target_server,
                "target_tool": flow.target_tool,
                "target_field": flow.target_field,
                "category": flow.category,
                "details": flow.details,
                "mismatch_type": flow.mismatch_type,
                "severity": flow.severity,
            }
            for flow in record.flows
        ],
        "local_diagnostic": record.local_diagnostic.to_dict(),
        "receipt": record.receipt.to_dict(),
    }
    # Epistemic receipt: narrow product-facing view (local, not session-wide)
    rg = record.local_diagnostic.repair_geometry
    if rg is not None:
        d["epistemic_receipt"] = rg.epistemic_view().to_dict()
    return d


def _cmd_proxy_dispatch(args: argparse.Namespace) -> None:
    """Route `bulla proxy` to the live shim, replayer, or prompt-injector.

    Branching:
      - ``--inject-prompt`` → print bulla/agents/system_prompt_v1.md and
        exit. No backends are spawned.
      - ``--manifests`` set → legacy trace-replayer path. Emits a
        deprecation warning and forwards to ``_cmd_proxy``.
      - otherwise → live MCP proxy: parse ``--config`` or positional
        commands, run ``live_proxy.serve``.
    """
    if getattr(args, "inject_prompt", False):
        _emit_system_prompt()
        return
    if args.manifests is not None:
        print(
            "[bulla] deprecation: `bulla proxy --manifests ...` is the old "
            "trace replayer; use `bulla replay` instead. Forwarding for now.",
            file=sys.stderr,
        )
        _cmd_proxy(args)
        return
    _cmd_proxy_live(args)


def _emit_system_prompt() -> None:
    """Print the v1 agent system-prompt fragment to stdout.

    Reads from the in-package copy at
    ``bulla.agents.system_prompt_v1`` via ``importlib.resources`` so
    the command works for both source checkouts and pip-installed
    users.
    """
    try:
        from bulla.agents import get_system_prompt_v1
        sys.stdout.write(get_system_prompt_v1())
        return
    except Exception as exc:
        print(
            f"[bulla] could not load agent system prompt: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


def _cmd_proxy_live(args: argparse.Namespace) -> None:
    """Run the live MCP proxy."""
    import asyncio

    from bulla.live_proxy import serve

    backend_specs: list[tuple[str, str, dict[str, str] | None]] = []
    used_names: set[str] = set()
    if args.config is not None:
        backend_specs.extend(_parse_live_proxy_config(args.config))
        used_names.update(spec[0] for spec in backend_specs)
    for i, cmd in enumerate(args.commands or []):
        base = _guess_server_name(cmd, i)
        name = base
        suffix = 1
        while name in used_names:
            name = f"{base}_{suffix}"
            suffix += 1
        used_names.add(name)
        backend_specs.append((name, cmd, None))
    if not backend_specs:
        print(
            "Error: `bulla proxy` needs at least one backend. "
            "Pass server commands positionally (after `--`) or use "
            "`--config servers.yaml`.",
            file=sys.stderr,
        )
        sys.exit(2)
    telemetry_path = getattr(args, "telemetry_out", None)
    signer = None
    if getattr(args, "key", None) is not None:
        signer = _load_signer_or_exit(args.key, getattr(args, "issuer", None))
    registry = _open_registry(getattr(args, "registry", None))
    mandate = None
    if getattr(args, "mandate_principal", None):
        mandate = {
            "principal": args.mandate_principal,
            "policy": getattr(args, "mandate_policy", None) or "sha256:unspecified",
        }
    try:
        asyncio.run(serve(
            backend_specs,
            telemetry_path=telemetry_path,
            signer=signer,
            registry=registry,
            enforce=getattr(args, "enforce", False),
            trusted_root=getattr(args, "trusted_root", None),
            shadow=getattr(args, "shadow", False),
            mandate=mandate,
            gate_reads=getattr(args, "gate_reads", False),
        ))
    except KeyboardInterrupt:
        pass


def _open_registry(spec):
    """Open a deed registry from a CLI spec: an ``http(s)://`` URL -> a read-only
    ``HttpRegistry`` (verify/lookup only); any other value -> a local appendable
    ``DeedLog`` (emit + verify + lookup). ``None`` -> ``None`` (no deed surface)."""
    if not spec:
        return None
    if str(spec).startswith(("http://", "https://")):
        from bulla.http_registry import HttpRegistry
        return HttpRegistry(str(spec))
    from bulla.registry import DeedLog
    return DeedLog(Path(spec))


def _parse_live_proxy_config(
    path: Path,
) -> list[tuple[str, str, dict[str, str] | None]]:
    """Parse a YAML config: ``{servers: {name: {command, env}}}``."""
    import yaml

    data = yaml.safe_load(path.read_text()) or {}
    servers = data.get("servers", {}) or {}
    if not isinstance(servers, dict):
        raise SystemExit(
            f"Error: {path}: top-level `servers` must be a mapping"
        )
    out: list[tuple[str, str, dict[str, str] | None]] = []
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            raise SystemExit(
                f"Error: {path}: server {name!r} must be a mapping"
            )
        command = cfg.get("command", "")
        env = cfg.get("env") or None
        if not command:
            raise SystemExit(
                f"Error: {path}: server {name!r} has no `command`"
            )
        out.append((str(name), str(command), env))
    return out


def _guess_server_name(command: str, index: int) -> str:
    """Pick a stable name for a positional backend command.

    Skips interpreter / wrapper prefixes (``python``, ``npx``, ``bunx``,
    ``uvx``, ``node``) and uses the script/package name. Falls back to
    ``server_<i>`` if no usable identifier can be extracted.
    """
    import re
    import shlex

    parts = shlex.split(command)
    if not parts:
        return f"server_{index}"
    interpreter = re.compile(r"^(python[0-9.]*|node|bunx?|npx|uvx?)$")
    token = parts[0]
    if interpreter.match(parts[0]):
        for p in parts[1:]:
            if not p.startswith("-"):
                token = p
                break
    bare = token.rsplit("/", 1)[-1].split("@")[-1]
    if "/" in token and not bare:
        bare = token.rsplit("/", 1)[-1]
    for suffix in (".py", ".js", ".ts", ".mjs"):
        if bare.endswith(suffix):
            bare = bare[: -len(suffix)]
            break
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", bare).strip("_")
    if not cleaned:
        return f"server_{index}"
    return cleaned


def _cmd_proxy(args: argparse.Namespace) -> None:
    """Replay a composition-aware proxy trace against captured manifests."""
    from bulla.proxy import BullaProxySession

    server_tools = _load_manifest_dir(args.manifests)
    trace_data = json.loads(args.trace.read_text())
    calls = trace_data["calls"] if isinstance(trace_data, dict) else trace_data
    if not isinstance(calls, list):
        print("Error: trace must be a JSON array or an object with a 'calls' array.", file=sys.stderr)
        sys.exit(1)

    trace_servers = {
        item["server"]
        for item in calls
        if isinstance(item, dict) and "server" in item
    }
    server_tools = {
        name: tools
        for name, tools in server_tools.items()
        if name in trace_servers
    }

    try:
        session = BullaProxySession(server_tools)
        records = session.replay_trace(calls)
    except (KeyError, TypeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    baseline = session.baseline
    output = {
        "trace_name": (
            trace_data.get("name", args.trace.stem)
            if isinstance(trace_data, dict)
            else args.trace.stem
        ),
        "baseline": {
            "coherence_fee": baseline.diagnostic.coherence_fee,
            "blind_spots": len(baseline.diagnostic.blind_spots),
            "boundary_fee": (
                None
                if baseline.decomposition is None
                else baseline.decomposition.boundary_fee
            ),
            "disposition": baseline.receipt.disposition.value,
        },
        "calls": [_proxy_record_to_dict(record) for record in records],
        "final_receipt": session.current_receipt.to_dict(),
        "flow_conflicts": [conflict.to_dict() for conflict in session.flow_conflicts],
    }

    if args.format == "json":
        text = json.dumps(output, indent=2)
    else:
        lines = [
            f"Trace: {output['trace_name']}",
            (
                "Baseline: "
                f"fee={output['baseline']['coherence_fee']} "
                f"blind_spots={output['baseline']['blind_spots']} "
                f"boundary_fee={output['baseline']['boundary_fee']} "
                f"disposition={output['baseline']['disposition']}"
            ),
            "",
        ]
        for record in records:
            local = record.local_diagnostic
            lines.append(
                f"[{record.call_id}] {record.server}.{record.tool} "
                f"local_fee={local.coherence_fee} "
                f"betti_1={local.betti_1} "
                f"cluster_calls={list(local.cluster_call_ids)}"
            )
            if record.flows:
                for flow in record.flows:
                    lines.append(
                        "  "
                        f"{flow.source_server}.{flow.source_tool}.{flow.source_field} -> "
                        f"{flow.target_server}.{flow.target_tool}.{flow.target_field} "
                        f"[{flow.category}]"
                    )
            else:
                lines.append("  (no traced flows)")
            lines.append(
                "  "
                f"receipt={record.receipt.receipt_hash[:12]} "
                f"disposition={record.receipt.disposition.value}"
            )
            lines.append("")
        text = "\n".join(lines).rstrip() + "\n"

    if args.output is not None:
        args.output.write_text(text)
    else:
        print(text, end="" if text.endswith("\n") else "\n")


def _cmd_serve() -> None:
    from bulla.serve import run_server
    run_server()


# ── gauge ─────────────────────────────────────────────────────────────


def _gauge_text(
    diag: "Diagnostic",
    disclosure: list[tuple[str, str]],
    basis: "WitnessBasis | None",
    verbose: bool = False,
) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append(f"  {diag.name}")
    lines.append(f"  {'─' * len(diag.name)}")
    lines.append(f"  {diag.n_tools} tools, {diag.n_edges} edges")
    lines.append("")
    lines.append(f"  Coherence fee:  {diag.coherence_fee}")
    lines.append(f"  Blind spots:    {len(diag.blind_spots)}")
    lines.append(f"  Bridges:        {len(diag.bridges)}")

    if verbose and diag.blind_spots:
        lines.append("")
        lines.append(f"  Blind spot detail ({len(diag.blind_spots)}):")
        for i, bs in enumerate(diag.blind_spots, 1):
            locs: list[str] = []
            if bs.from_hidden:
                locs.append(f"{bs.from_field} hidden at {bs.from_tool}")
            if bs.to_hidden:
                locs.append(f"{bs.to_field} hidden at {bs.to_tool}")
            lines.append(f"    [{i}] {bs.dimension} ({bs.edge})")
            lines.append(f"        {'; '.join(locs)}")

    if disclosure:
        lines.append("")
        lines.append(f"  Disclosure set ({len(disclosure)} field(s) to expose):")
        for i, (tool, field) in enumerate(disclosure, 1):
            lines.append(f"    {i}. {tool}.{field}")
    elif diag.coherence_fee == 0:
        lines.append("")
        lines.append("  No disclosures needed.")

    if verbose and diag.bridges:
        from bulla.model import Bridge
        seen_fields: set[str] = set()
        unique_bridges: list[Bridge] = []
        for br in diag.bridges:
            if br.field not in seen_fields:
                merged: list[str] = []
                for b2 in diag.bridges:
                    if b2.field == br.field:
                        for t in b2.add_to:
                            if t not in merged:
                                merged.append(t)
                unique_bridges.append(
                    Bridge(field=br.field, add_to=merged, eliminates=br.eliminates)
                )
                seen_fields.add(br.field)
        lines.append("")
        lines.append("  Recommended bridges:")
        for i, br in enumerate(unique_bridges, 1):
            tools_str = " and ".join(f"F({t})" for t in br.add_to)
            lines.append(f"    [{i}] Add '{br.field}' to {tools_str}")

    if basis is not None:
        lines.append("")
        disc_str = f", {basis.discovered} discovered" if basis.discovered > 0 else ""
        lines.append(
            f"  Witness basis: {basis.declared} declared, "
            f"{basis.inferred} inferred, {basis.unknown} unknown{disc_str}"
        )

    lines.append("")
    return "\n".join(lines)


def _gauge_json(
    diag: "Diagnostic",
    disclosure: list[tuple[str, str]],
    basis: "WitnessBasis | None",
) -> str:
    from datetime import datetime, timezone as tz

    obj: dict = {
        "name": diag.name,
        "bulla_version": __version__,
        "timestamp": datetime.now(tz.utc).isoformat(),
        "topology": {
            "tools": diag.n_tools,
            "edges": diag.n_edges,
            "betti_1": diag.betti_1,
        },
        "coherence_fee": diag.coherence_fee,
        "blind_spots_count": len(diag.blind_spots),
        "bridges_count": len(diag.bridges),
        "n_unbridged": diag.n_unbridged,
        "disclosure_set": [[t, f] for t, f in disclosure],
        "witness_basis": basis.to_dict() if basis is not None else None,
        "blind_spots": [
            {
                "dimension": bs.dimension,
                "edge": bs.edge,
                "from_tool": bs.from_tool,
                "to_tool": bs.to_tool,
                "from_field": bs.from_field,
                "to_field": bs.to_field,
                "from_hidden": bs.from_hidden,
                "to_hidden": bs.to_hidden,
            }
            for bs in diag.blind_spots
        ],
    }
    return json.dumps(obj, indent=2)


def _gauge_threshold_check(
    diag: "Diagnostic",
    args: argparse.Namespace,
    *,
    unmet_count: int = 0,
    contradiction_count: int = 0,
    structural_contradiction_score: int = 0,
) -> None:
    violations: list[str] = []
    max_fee = getattr(args, "max_fee", None)
    max_bs = getattr(args, "max_blind_spots", None)
    max_unmet = getattr(args, "max_unmet", None)
    max_contradictions = getattr(args, "max_contradictions", None)
    max_structural = getattr(args, "max_structural", None)
    if max_fee is not None and diag.coherence_fee > max_fee:
        violations.append(
            f"fee {diag.coherence_fee} exceeds --max-fee {max_fee}"
        )
    if max_bs is not None and len(diag.blind_spots) > max_bs:
        violations.append(
            f"{len(diag.blind_spots)} blind spot(s) exceeds "
            f"--max-blind-spots {max_bs}"
        )
    if max_unmet is not None and unmet_count > max_unmet:
        violations.append(
            f"{unmet_count} unmet obligation(s) exceeds "
            f"--max-unmet {max_unmet}"
        )
    if max_contradictions is not None and contradiction_count > max_contradictions:
        violations.append(
            f"{contradiction_count} contradiction(s) exceeds "
            f"--max-contradictions {max_contradictions}"
        )
    if max_structural is not None and structural_contradiction_score > max_structural:
        violations.append(
            f"structural contradiction score {structural_contradiction_score} exceeds "
            f"--max-structural {max_structural}"
        )
    if violations:
        for v in violations:
            print(f"FAIL: {v}", file=sys.stderr)
        sys.exit(1)


def _cmd_gauge(args: argparse.Namespace) -> None:
    _configure_packs_from_args(args)
    from bulla.guard import BullaGuard
    from bulla.scan import ScanError

    try:
        if getattr(args, "mcp_server", None):
            guard = BullaGuard.from_mcp_server(args.mcp_server)
        else:
            guard = BullaGuard.from_mcp_manifest(args.manifest)
    except (ScanError, ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    leverage_flag = getattr(args, "leverage", False)
    substitutes_arg = getattr(args, "substitutes", None)
    costs_path = getattr(args, "costs", None)
    include_witness = bool(
        leverage_flag or substitutes_arg or costs_path
    )

    from bulla.diagnostic import diagnose as _diagnose_fn
    diag = _diagnose_fn(
        guard.composition,
        include_witness_geometry=include_witness,
    )

    from bulla.diagnostic import prescriptive_disclosure
    disclosure = prescriptive_disclosure(guard.composition, diag.coherence_fee)

    # ── --substitutes TOOL FIELD ──────────────────────────────────────
    substitutes_output: str | None = None
    if substitutes_arg is not None:
        sub_tool, sub_field = substitutes_arg
        target = (sub_tool, sub_field)
        if target not in diag.hidden_basis:
            print(
                f"Error: field ({sub_tool!r}, {sub_field!r}) is not a "
                f"hidden field in this composition. Hidden fields: "
                f"{sorted(diag.hidden_basis)}",
                file=sys.stderr,
            )
            sys.exit(1)
        from bulla.witness_geometry import (
            compute_all as _wg_compute_all,
            disclosure_substitutes as _wg_substitutes,
        )
        wg = _wg_compute_all(
            list(guard.composition.tools),
            list(guard.composition.edges),
        )
        subs = _wg_substitutes(
            wg["K"], wg["hidden_basis"], target, k=3
        )
        substitutes_output = _format_substitutes(target, subs)

    # ── --costs costs.yaml ────────────────────────────────────────────
    weighted_basis: list[tuple[str, str]] | None = None
    weighted_total: "Fraction | None" = None
    if costs_path is not None:
        import yaml as _yaml
        from fractions import Fraction as _Fraction
        try:
            raw = _yaml.safe_load(costs_path.read_text())
        except FileNotFoundError:
            print(f"Error: costs file not found: {costs_path}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(raw, dict):
            print(
                f"Error: costs file must be a YAML mapping of "
                f"'<tool>:<field>' -> cost. Got {type(raw).__name__}.",
                file=sys.stderr,
            )
            sys.exit(1)
        costs: dict[tuple[str, str], _Fraction] = {}
        for key, val in raw.items():
            if not isinstance(key, str) or ":" not in key:
                print(
                    f"Error: cost key {key!r} must be of the form "
                    f"'<tool>:<field>'.",
                    file=sys.stderr,
                )
                sys.exit(1)
            tool, _, field = key.partition(":")
            try:
                costs[(tool, field)] = _Fraction(str(val))
            except (ValueError, TypeError) as exc:
                print(
                    f"Error: cost for {key!r} is not a rational: {val!r} "
                    f"({exc}).",
                    file=sys.stderr,
                )
                sys.exit(1)
        from bulla.witness_geometry import (
            compute_all as _wg_compute_all,
            weighted_greedy_repair as _wg_greedy,
        )
        wg = _wg_compute_all(
            list(guard.composition.tools),
            list(guard.composition.edges),
        )
        weighted_basis = _wg_greedy(wg["K"], wg["hidden_basis"], costs)
        weighted_total = sum(
            (costs.get(p, _Fraction(1)) for p in weighted_basis),
            start=_Fraction(0),
        )

    fmt = getattr(args, "format", "text")
    verbose = getattr(args, "verbose", False)
    if fmt == "json":
        print(_gauge_json(diag, disclosure, guard.witness_basis))
        if substitutes_output is not None:
            print(substitutes_output)
        if weighted_basis is not None:
            print(_format_weighted_basis_json(weighted_basis, weighted_total))
    elif fmt == "sarif":
        print(guard.to_sarif())
    else:
        print(_gauge_text(diag, disclosure, guard.witness_basis, verbose=verbose))
        if leverage_flag and diag.leverage_scores:
            from bulla.formatters import _format_witness_section
            witness_section = _format_witness_section(diag)
            if witness_section:
                print(witness_section)
        if substitutes_output is not None:
            print(substitutes_output)
        if weighted_basis is not None:
            print(_format_weighted_basis_text(weighted_basis, weighted_total))

    output_comp = getattr(args, "output_composition", None)
    if output_comp:
        guard.to_yaml(output_comp)
        print(f"Wrote composition to {output_comp}", file=sys.stderr)

    _gauge_threshold_check(diag, args)


def _format_substitutes(
    target: tuple[str, str],
    subs: list[tuple[tuple[str, str], "Fraction"]],
) -> str:
    """Render `--substitutes` output as a text block."""
    tool, field = target
    lines: list[str] = []
    lines.append("")
    lines.append(f"  Disclosure substitutes for {tool}.{field}:")
    if not subs:
        lines.append("    (no substitutes in the same hidden component)")
    else:
        for i, (field_pair, r_eff) in enumerate(subs, 1):
            sub_tool, sub_field = field_pair
            if hasattr(r_eff, "numerator") and r_eff.denominator == 1:
                r_str = f"{r_eff.numerator}"
            elif hasattr(r_eff, "numerator"):
                r_str = f"{r_eff.numerator}/{r_eff.denominator}"
            else:
                r_str = str(r_eff)
            lines.append(
                f"    [{i}] {sub_tool}.{sub_field}    R_eff = {r_str}"
            )
    return "\n".join(lines)


def _format_weighted_basis_text(
    basis: list[tuple[str, str]],
    total_cost: "Fraction | None",
) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append(
        f"  Minimum-cost disclosure basis ({len(basis)} field"
        f"{'s' if len(basis) != 1 else ''}):"
    )
    for i, (tool, field) in enumerate(basis, 1):
        lines.append(f"    [{i}] {tool}.{field}")
    if total_cost is not None:
        if hasattr(total_cost, "denominator") and total_cost.denominator == 1:
            cost_str = f"{total_cost.numerator}"
        elif hasattr(total_cost, "denominator"):
            cost_str = (
                f"{total_cost.numerator}/{total_cost.denominator}"
            )
        else:
            cost_str = str(total_cost)
        lines.append(f"  Total cost: {cost_str}")
    return "\n".join(lines)


def _format_weighted_basis_json(
    basis: list[tuple[str, str]],
    total_cost: "Fraction | None",
) -> str:
    payload = {
        "weighted_greedy_basis": [list(pair) for pair in basis],
        "total_cost": (
            None if total_cost is None else str(total_cost)
        ),
    }
    return json.dumps(payload, indent=2)


# ── audit ─────────────────────────────────────────────────────────────


def _audit_guided_repair_append(guided_repair: dict | None) -> str:
    """Append guided repair / convergence narrative (keeps sprint-29 checks)."""
    if not guided_repair:
        return ""
    lines: list[str] = []
    lines.append("")
    orig = guided_repair["original_fee"]
    rep = guided_repair["repaired_fee"]
    conf = guided_repair["confirmed"]
    den = guided_repair["denied"]
    unc = guided_repair["uncertain"]
    n_rounds = guided_repair.get("rounds")
    reason = guided_repair.get("termination_reason")
    if n_rounds is not None:
        lines.append(
            f"  Convergence: fee {orig} -> {rep} in {n_rounds} round(s) "
            f"({conf} confirmed, {den} denied, {unc} uncertain) [{reason}]"
        )
    else:
        lines.append(
            f"  Guided repair: fee {orig} -> {rep} "
            f"({conf} confirmed, {den} denied, {unc} uncertain)"
        )

    disc_pack = guided_repair.get("discovered_pack")
    if disc_pack:
        from bulla.repair import detect_contradictions

        disc_dims = disc_pack.get("dimensions", {})
        n_vals = sum(len(d.get("known_values", [])) for d in disc_dims.values())
        contradictions = detect_contradictions(disc_pack)
        lines.append(
            f"  Discovered conventions: {len(disc_dims)} dimension(s) "
            f"with {n_vals} value(s)"
        )

        probe_tool_values: dict[str, dict[str, str]] = {}
        for p in guided_repair.get("probes", []):
            obl = p.get("obligation", {})
            dim = obl.get("dimension", "")
            tool = obl.get("placeholder_tool", "")
            val = p.get("convention_value", "")
            if dim and tool and val and p.get("verdict") == "CONFIRMED":
                probe_tool_values.setdefault(dim, {})[tool] = val

        contradiction_dims = {c.dimension for c in contradictions}
        for dname, ddef in disc_dims.items():
            vals = ddef.get("known_values", [])
            if dname in contradiction_dims:
                lines.append(f"    {dname}: MISMATCH")
                tv = probe_tool_values.get(dname, {})
                max_prefix = max((len(t.split("__")[0]) for t in tv), default=0)
                for tool_name, value in tv.items():
                    prefix = tool_name.split("__")[0]
                    lines.append(f"      {prefix:<{max_prefix}s}: {value}")
            else:
                tools = ddef.get("provenance", {}).get("source_tools", [])
                tool_str = f" (from {', '.join(tools)})" if tools else ""
                lines.append(f"    {dname}: {', '.join(vals)}{tool_str}")

        if contradictions:
            lines.append(
                f"  {len(contradictions)} convention mismatch(es) across server boundaries"
            )

    return "\n".join(lines) + ("\n" if lines else "")


def _audit_verbose_metadata_append(
    diag: "Diagnostic",
    disclosure: list[tuple[str, str]],
    basis: "WitnessBasis | None",
    decomposition: "FeeDecomposition | None",
    own_obligations: tuple | None,
    obligation_check: dict | None,
    repair_geometry: "RepairGeometry | None",
) -> str:
    """Extra audit sections reserved for ``-v`` (receipt stays compact)."""
    lines: list[str] = []

    if disclosure:
        lines.append("")
        lines.append(f"  Disclosure set ({len(disclosure)} field(s) to expose):")
        for i, (tool, field) in enumerate(disclosure, 1):
            lines.append(f"    {i}. {tool}.{field}")
    elif diag.coherence_fee == 0:
        lines.append("")
        lines.append("  No disclosures needed.")

    lines.append("")
    lines.append(
        f"  Composition detail: {diag.n_tools} tools, {diag.n_edges} edges — "
        f"total coherence fee {diag.coherence_fee}, "
        f"{len(diag.blind_spots)} blind spot(s), {len(diag.bridges)} bridge(s)"
    )

    if repair_geometry is not None:
        er = repair_geometry.epistemic_view()
        lines.append("")
        lines.append(f"  Repair regime:  {er.regime}")
        if er.regime == "exact":
            lines.append(
                f"    Geometry dividend: {er.geometry_dividend}  (provably optimal)"
            )
        else:
            lines.append(
                f"    Geometry dividend: {er.geometry_dividend}  (approximation)"
            )
            if er.downgrade:
                lines.append(f"    Downgrade reason:  {er.downgrade}")
            if er.forced_cost is not None:
                lines.append(f"    Forced cost:       {er.forced_cost}")

    if basis is not None:
        lines.append("")
        disc_str = f", {basis.discovered} discovered" if basis.discovered > 0 else ""
        lines.append(
            f"  Witness basis: {basis.declared} declared, "
            f"{basis.inferred} inferred, {basis.unknown} unknown{disc_str}"
        )

    if own_obligations:
        bf = decomposition.boundary_fee if decomposition else "?"
        lines.append("")
        lines.append(
            f"  Obligations ({len(own_obligations)} from boundary_fee={bf}):"
        )
        for obl in own_obligations:
            lines.append(
                f"    - {obl.dimension}: field \"{obl.field}\" hidden in "
                f"{obl.placeholder_tool} group ({obl.source_edge})"
            )

    if obligation_check:
        lines.append("")
        lines.append(f"  Parent obligations: {obligation_check['parent_total']}")
        met_dims = ", ".join(o["dimension"] for o in obligation_check["met_obligations"])
        unmet_dims = ", ".join(
            f"{o['dimension']} -- propagated"
            for o in obligation_check["unmet_obligations"]
        )
        lines.append(
            f"    Met: {obligation_check['met']}"
            + (f" ({met_dims})" if met_dims else "")
        )
        lines.append(
            f"    Unmet: {obligation_check['unmet']}"
            + (f" ({unmet_dims})" if unmet_dims else "")
        )
        lines.append(f"    Irrelevant: {obligation_check['irrelevant']}")

    if not lines:
        return ""
    return "\n".join(lines).rstrip() + "\n"


def _audit_text(
    server_results: list,
    diag: "Diagnostic",
    disclosure: list[tuple[str, str]],
    basis: "WitnessBasis | None",
    decomposition: "FeeDecomposition | None",
    verbose: bool = False,
    own_obligations: tuple | None = None,
    obligation_check: dict | None = None,
    guided_repair: dict | None = None,
    repair_geometry: "RepairGeometry | None" = None,
    *,
    raw_tools: list | None = None,
    context_line: str | None = None,
) -> str:
    ok_count = sum(1 for r in server_results if r.ok)
    if ok_count == 0:
        lines = ["bulla audit", "───────────", "", "  No servers scanned successfully.", ""]
        return "\n".join(lines)

    report = build_audit_report(
        server_results,
        diag,
        decomposition,
        raw_tools=raw_tools,
        guided_repair=guided_repair,
        context_line=context_line,
        disclosure=disclosure,
    )
    text = format_audit_report_text(
        report,
        verbose=verbose,
        blind_spots=diag.blind_spots if verbose else None,
    )
    if verbose:
        text += format_verbose_blind_spots(diag.blind_spots)
        text += _audit_verbose_metadata_append(
            diag,
            disclosure,
            basis,
            decomposition,
            own_obligations,
            obligation_check,
            repair_geometry,
        )
    text += _audit_guided_repair_append(guided_repair)
    return text


def _audit_json(
    server_results: list,
    diag: "Diagnostic",
    disclosure: list[tuple[str, str]],
    basis: "WitnessBasis | None",
    decomposition: "FeeDecomposition | None",
    own_obligations: tuple | None = None,
    obligation_check: dict | None = None,
    guided_repair: dict | None = None,
    repair_geometry: "RepairGeometry | None" = None,
    *,
    raw_tools: list | None = None,
    context_line: str | None = None,
) -> str:
    from datetime import datetime, timezone as tz

    servers_out = []
    for r in server_results:
        entry: dict = {"name": r.name, "status": "ok" if r.ok else "failed"}
        if r.ok:
            entry["tools_count"] = len(r.tools)
        if r.error:
            entry["error"] = r.error
        servers_out.append(entry)

    obj: dict = {
        "name": "audit",
        "bulla_version": __version__,
        "timestamp": datetime.now(tz.utc).isoformat(),
        "servers": servers_out,
        "topology": {
            "tools": diag.n_tools,
            "edges": diag.n_edges,
            "betti_1": diag.betti_1,
        },
        "coherence_fee": diag.coherence_fee,
        "blind_spots_count": len(diag.blind_spots),
        "bridges_count": len(diag.bridges),
        "n_unbridged": diag.n_unbridged,
        "disclosure_set": [[t, f] for t, f in disclosure],
        "witness_basis": basis.to_dict() if basis is not None else None,
    }
    if repair_geometry is not None:
        obj["epistemic_receipt"] = repair_geometry.epistemic_view().to_dict()
    if decomposition:
        obj["cross_server_decomposition"] = {
            "intra_server_fee": sum(decomposition.local_fees),
            "boundary_fee": decomposition.boundary_fee,
            "local_fees": list(decomposition.local_fees),
            "partition": [sorted(g) for g in decomposition.partition],
        }
    if own_obligations:
        obj["boundary_obligations"] = [o.to_dict() for o in own_obligations]
    if obligation_check:
        obj["obligation_check"] = obligation_check
    if guided_repair:
        from bulla.repair import detect_contradictions

        obj["guided_repair"] = guided_repair
        disc_pack = guided_repair.get("discovered_pack")
        if disc_pack:
            contradictions = detect_contradictions(disc_pack)
            obj["guided_repair"]["mismatches"] = len(contradictions)
            obj["guided_repair"]["contradictions"] = [
                c.to_dict() for c in contradictions
            ]
    obj["audit_report"] = audit_report_to_json_dict(
        build_audit_report(
            server_results,
            diag,
            decomposition,
            raw_tools=raw_tools,
            guided_repair=guided_repair,
            context_line=context_line,
            disclosure=disclosure,
        )
    )
    return json.dumps(obj, indent=2)


def _load_manifests_dir(manifests_dir: Path) -> tuple[list[dict], list[str]]:
    """Load all *.json manifest files from a directory.

    Returns (all_tools, server_names) with tools prefixed by server name.
    """
    server_names: list[str] = []
    all_tools: list[dict] = []
    for manifest_file in sorted(manifests_dir.glob("*.json")):
        with open(manifest_file) as f:
            data = json.load(f)
        tools_data = data.get("tools", data) if isinstance(data, dict) else data
        if not isinstance(tools_data, list):
            continue
        server = manifest_file.stem
        server_names.append(server)
        for t in tools_data:
            t["name"] = f"{server}__{t.get('name', 'unknown')}"
        all_tools.extend(tools_data)
    return all_tools, server_names


def _cmd_frameworks_list(args: argparse.Namespace) -> None:
    """List all registered framework adapters and their parse-mode support."""
    from bulla.frameworks import all_frameworks, ParseMode

    print(f"{'FRAMEWORK':<22}  {'STATIC':<7}  {'RUNTIME':<8}  DISPLAY NAME")
    print(f"{'-' * 22}  {'-' * 7}  {'-' * 8}  {'-' * 30}")
    for fw in all_frameworks():
        s = "yes" if fw.supports(ParseMode.STATIC) else "no"
        r = "future" if not fw.supports(ParseMode.RUNTIME) else "yes"
        print(f"{fw.name:<22}  {s:<7}  {r:<8}  {fw.display_name}")
    print()
    print("Use 'bulla import <framework> <source>' to convert to a Bulla manifest.")


def _cmd_showcase(args: argparse.Namespace) -> None:
    """Run the full algebraic repair loop demo on bundled MCP manifests."""
    from bulla.showcase import run_showcase

    run_showcase(json_output=getattr(args, "json", False))


def _cmd_import(args: argparse.Namespace) -> None:
    """Convert framework-native tool definitions to Bulla manifests."""
    import json
    import sys
    from bulla.frameworks import FrameworkError, ParseMode, get, tools_to_raw_dicts

    framework_name = args.framework
    source: str = args.source
    out_path: Path | None = getattr(args, "out", None)
    do_audit: bool = getattr(args, "audit", False)
    mode_str: str = getattr(args, "mode", "static")

    try:
        mode = ParseMode(mode_str)
    except ValueError:
        print(f"Error: unknown --mode {mode_str!r}. Use 'static' or 'runtime'.", file=sys.stderr)
        sys.exit(1)

    if mode is ParseMode.RUNTIME:
        print(
            "Error: --mode runtime is reserved for a future sprint. "
            "Use --mode static (default).",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        adapter = get(framework_name)
    except FrameworkError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if source == "-":
        # stdin → write to a temp file because adapters take a Path
        import tempfile
        suffix = ".py" if framework_name in ("langgraph", "crewai") else ".json"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, delete=False
        ) as tmp:
            tmp.write(sys.stdin.read())
            source_path: Path | None = Path(tmp.name)
    else:
        source_path = Path(source)

    try:
        tools = adapter.parse(source_path, mode=mode)
    except FrameworkError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except NotImplementedError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

    raw = tools_to_raw_dicts(tools)

    if do_audit:
        # Generate a manifest JSON via the existing pipeline and run audit.
        from bulla.manifest import generate_manifest_from_tools

        manifests = generate_manifest_from_tools(raw)
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_dir = Path(tmpdir)
            for m in manifests:
                tool_name = m.get("tool", {}).get("name", "tool")
                (tmp_dir / f"{tool_name}.json").write_text(json.dumps(m, indent=2))
            audit_args = argparse.Namespace(
                manifests=tmp_dir,
                config=None,
                discover=False,
                receipt=None,
                chain=None,
                format=getattr(args, "format", "text"),
                verbose=False,
                skip_failed=True,
                discover_provider="auto",
                output_discovered=None,
                guided_discover=False,
                converge=False,
                max_rounds=5,
                max_fee=None,
                max_blind_spots=None,
                max_unmet=None,
                max_contradictions=None,
                max_structural=None,
                output_composition=None,
                packs=[],
                no_default_packs=False,
                host=None,
            )
            _cmd_audit(audit_args)
        return

    # Default: write the raw tools list as a single manifest JSON file the
    # rest of bulla can consume (matches the MCP tools/list shape).
    payload = {"tools": raw}
    text = json.dumps(payload, indent=2)
    if out_path:
        out_path.write_text(text)
        print(f"Wrote {len(raw)} tool(s) to {out_path}", file=sys.stderr)
    else:
        print(text)


def _cmd_hosts_list(args: argparse.Namespace) -> None:
    """List all registered MCP hosts; mark which have configs detected.

    With ``--verbose`` / ``-v``, show every candidate path scanned per host
    and the reason it did or did not match. Useful for debugging
    "why isn't my Cline-in-Insiders showing up?" cases.

    With ``--format json``, emits the same data model as a structured
    document for CI / wrapper-tool consumption. JSON output always
    includes the full per-path probe data (i.e. behaves like ``-v``).
    """
    import json
    from bulla.hosts import all_hosts, detect_all, diagnose_path

    verbose = bool(getattr(args, "verbose", False))
    output_format = getattr(args, "format", "text") or "text"
    host_filter = getattr(args, "host", None)

    detected = {(d.host.name, d.path) for d in detect_all()}

    if output_format == "json":
        _emit_hosts_list_json(host_filter, detected, all_hosts, diagnose_path)
        return

    detected_hosts = {h for h, _ in detected}

    if not verbose:
        print(f"{'HOST':<18}  {'STATUS':<10}  PATH")
        print(f"{'-' * 18}  {'-' * 10}  {'-' * 40}")
        for host in all_hosts():
            if host_filter and host.name != host_filter:
                continue
            if host.name in detected_hosts:
                paths = sorted(p for h, p in detected if h == host.name)
                for path in paths:
                    print(f"{host.name:<18}  {'detected':<10}  {path}")
            else:
                print(f"{host.name:<18}  {'-':<10}  (no config found on this system)")
        print()
        print(
            f"{len(detected)} config(s) detected across "
            f"{len(set(h.name for h in all_hosts()))} registered host(s)."
        )
        if not host_filter:
            print("Run with -v / --verbose to see every candidate path that was scanned.")
            print("Run with --format json for structured output.")
        return

    # Verbose mode: per-host scan trace (text)
    for host in all_hosts():
        if host_filter and host.name != host_filter:
            continue
        matches = sum(1 for h, _ in detected if h == host.name)
        status = (
            f"detected ({matches} config{'s' if matches > 1 else ''})"
            if matches
            else "not detected"
        )
        print(f"{host.display_name}  ({host.name})")
        print(f"  status: {status}")
        print(f"  paths checked:")
        for path in host.candidate_paths():
            probe = diagnose_path(host, path)
            mark = "✓" if probe.matched else "·" if probe.exists else "-"
            print(f"    {mark} {path}")
            print(f"        {probe.reason}")
        print()


def _emit_hosts_list_json(
    host_filter: str | None,
    detected: set,
    all_hosts_fn,
    diagnose_path_fn,
) -> None:
    """Emit `bulla hosts list` data as a structured JSON document.

    Schema:
        {
          "schema_version": "1",
          "hosts": [
            {
              "name": "cline",
              "display_name": "Cline",
              "status": "detected" | "not_detected",
              "matched_count": int,
              "paths": [
                {"path": str, "exists": bool, "matched": bool, "reason": str}
              ]
            }
          ],
          "total_detected": int,
          "total_hosts": int
        }
    """
    import json

    out_hosts: list[dict] = []
    for host in all_hosts_fn():
        if host_filter and host.name != host_filter:
            continue
        matches = sum(1 for h, _ in detected if h == host.name)
        path_probes: list[dict] = []
        for path in host.candidate_paths():
            probe = diagnose_path_fn(host, path)
            path_probes.append({
                "path": str(probe.path),
                "exists": probe.exists,
                "matched": probe.matched,
                "reason": probe.reason,
            })
        out_hosts.append({
            "name": host.name,
            "display_name": host.display_name,
            "status": "detected" if matches else "not_detected",
            "matched_count": matches,
            "paths": path_probes,
        })

    document = {
        "schema_version": "1",
        "hosts": out_hosts,
        "total_detected": sum(h["matched_count"] for h in out_hosts),
        "total_hosts": len(out_hosts),
    }
    print(json.dumps(document, indent=2))


def _cmd_audit(args: argparse.Namespace) -> None:
    _configure_packs_from_args(args)
    from bulla.diagnostic import (
        boundary_obligations_from_decomposition,
        check_obligations,
        decompose_fee,
        prescriptive_disclosure,
    )
    from bulla.formatters import format_sarif
    from bulla.guard import BullaGuard
    from bulla.infer.classifier import configure_packs, get_active_pack_refs

    manifests_dir: Path | None = getattr(args, "manifests", None)
    config_path = getattr(args, "config", None)
    scan_elapsed_s: float | None = None
    do_discover = getattr(args, "discover", False)
    chain_path: Path | None = getattr(args, "chain", None)
    receipt_path: Path | None = getattr(args, "receipt", None)

    if manifests_dir and config_path:
        print(
            "Error: Cannot use both --manifests and a config file.",
            file=sys.stderr,
        )
        sys.exit(1)

    if manifests_dir:
        if not manifests_dir.is_dir():
            print(f"Error: {manifests_dir} is not a directory.", file=sys.stderr)
            sys.exit(1)
        all_tools, server_names = _load_manifests_dir(manifests_dir)
        if not all_tools:
            print("Error: No tools found in manifest directory.", file=sys.stderr)
            sys.exit(1)
    else:
        from bulla.config import ConfigError, parse_mcp_config
        from bulla.hosts import HostError, detect_all, get
        from bulla.scan import scan_mcp_servers_parallel

        host_name = getattr(args, "host", None)
        chosen_host = None

        if config_path is None:
            if host_name:
                try:
                    chosen_host = get(host_name)
                except HostError as e:
                    print(f"Error: {e}", file=sys.stderr)
                    sys.exit(1)
                for path in chosen_host.candidate_paths():
                    if path.exists():
                        config_path = path
                        break
                if config_path is None:
                    print(
                        f"Error: No config found for host '{host_name}'. "
                        f"Pass an explicit config path.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
            else:
                matches = detect_all()
                if not matches:
                    print(
                        "No MCP configuration found.\n\n"
                        "Try with two real servers:\n"
                        "  bulla scan \\\n"
                        '    "npx -y @modelcontextprotocol/server-filesystem /tmp" \\\n'
                        '    "npx -y @modelcontextprotocol/server-git --repository /tmp"\n\n'
                        "Or point to a config:\n"
                        "  bulla audit ~/.cursor/mcp.json\n\n"
                        "Or run 'bulla hosts list' to see supported hosts.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                chosen_host = matches[0].host
                config_path = matches[0].path
            print(
                f"Auto-detected: {chosen_host.display_name} ({config_path})",
                file=sys.stderr,
            )
        elif host_name:
            try:
                chosen_host = get(host_name)
            except HostError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)

        try:
            if chosen_host is not None:
                entries = chosen_host.parse(config_path)
            else:
                entries = parse_mcp_config(config_path)
        except (ConfigError, HostError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        if not entries:
            print("Error: No stdio MCP servers found in config.", file=sys.stderr)
            sys.exit(1)

        servers_cfg = {
            e.name: {"command": e.command, "env": e.env or None}
            for e in entries
        }
        import time

        _t_scan = time.perf_counter()
        results = scan_mcp_servers_parallel(servers_cfg)
        scan_elapsed_s = time.perf_counter() - _t_scan

        skip_failed = getattr(args, "skip_failed", True)
        ok_results = [r for r in results if r.ok]
        failed = [r for r in results if not r.ok]

        if not ok_results:
            if not skip_failed and failed:
                print(f"Error: All {len(failed)} server(s) failed.", file=sys.stderr)
                for r in failed:
                    print(f"  {r.name}: {r.error}", file=sys.stderr)
                sys.exit(1)
            print("Error: No servers scanned successfully.", file=sys.stderr)
            sys.exit(1)

        all_tools = []
        server_names = []
        for r in ok_results:
            server_names.append(r.name)
            for tool in r.tools:
                tool["name"] = f"{r.name}__{tool.get('name', 'unknown')}"
            all_tools.extend(r.tools)

    # --chain: load prior receipt vocabulary before discovery/audit
    import tempfile
    chain_receipt_data: dict | None = None
    inherited_pack_path: Path | None = None
    if chain_path:
        if not chain_path.exists():
            print(f"Error: receipt not found: {chain_path}", file=sys.stderr)
            sys.exit(1)
        chain_receipt_data = json.loads(chain_path.read_text(encoding="utf-8"))
        inherited_dims = chain_receipt_data.get("inline_dimensions")
        if inherited_dims and isinstance(inherited_dims, dict):
            tmp = tempfile.NamedTemporaryFile(
                suffix=".yaml", mode="w", delete=False, prefix="bulla_chain_"
            )
            yaml.dump(inherited_dims, tmp, default_flow_style=False, sort_keys=False)
            tmp.close()
            inherited_pack_path = Path(tmp.name)

    # --discover: run LLM convention discovery
    discovered_pack_path: Path | None = None
    discovered_pack_data: dict | None = None
    discovery_n_dims = 0
    if do_discover:
        from bulla.discover.adapter import get_adapter
        from bulla.discover.engine import discover_dimensions

        provider = getattr(args, "discover_provider", "auto")
        try:
            adapter = get_adapter(provider)
        except (ValueError, ImportError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        existing_extra = list(getattr(args, "packs", None) or [])
        if inherited_pack_path:
            existing_extra.append(inherited_pack_path)

        try:
            disc_result = discover_dimensions(
                all_tools, adapter=adapter,
                existing_packs=existing_extra or None,
            )
        except (ValueError, ImportError) as e:
            print(f"Error during discovery: {e}", file=sys.stderr)
            sys.exit(1)

        if disc_result.valid and disc_result.n_dimensions > 0:
            discovered_pack_data = disc_result.pack
            discovery_n_dims = disc_result.n_dimensions

            output_disc = getattr(args, "output_discovered", None)
            if output_disc:
                output_disc.write_text(
                    yaml.dump(disc_result.pack, default_flow_style=False, sort_keys=False),
                    encoding="utf-8",
                )
                print(f"  Discovered pack saved to {output_disc}", file=sys.stderr)

            tmp = tempfile.NamedTemporaryFile(
                suffix=".yaml", mode="w", delete=False, prefix="bulla_disc_"
            )
            yaml.dump(disc_result.pack, tmp, default_flow_style=False, sort_keys=False)
            tmp.close()
            discovered_pack_path = Path(tmp.name)
        elif disc_result.valid and disc_result.n_dimensions == 0:
            print("  Discovery: no new dimensions found.", file=sys.stderr)
        else:
            print("  Discovery produced invalid output:", file=sys.stderr)
            for err in disc_result.errors:
                print(f"    [error] {err}", file=sys.stderr)

    # Reconfigure packs if discovery or chain added new packs
    extra_packs = list(getattr(args, "packs", None) or [])
    if inherited_pack_path:
        extra_packs.append(inherited_pack_path)
    if discovered_pack_path:
        extra_packs.append(discovered_pack_path)
    if extra_packs:
        configure_packs(extra_paths=extra_packs)

    import time as _time

    _t_diag = _time.perf_counter()
    guard = BullaGuard.from_tools_list(all_tools, name="audit")
    comp = guard.composition
    basis = guard.witness_basis

    tool_to_server = {
        t.name: t.name.split("__")[0] for t in comp.tools
    }

    from bulla.diagnostic import diagnose
    diag = diagnose(comp)
    disclosure = prescriptive_disclosure(comp, diag.coherence_fee)
    diag_elapsed_s = _time.perf_counter() - _t_diag

    decomposition = None
    if len(server_names) > 1:
        partition = []
        for sname in server_names:
            tools_in_server = frozenset(
                t_name for t_name, srv in tool_to_server.items()
                if srv == sname
            )
            if tools_in_server:
                partition.append(tools_in_server)
        all_tool_names = {t.name for t in comp.tools}
        covered = frozenset().union(*partition) if partition else frozenset()
        uncovered = all_tool_names - covered
        if uncovered:
            partition.append(frozenset(uncovered))
        if len(partition) > 1:
            decomposition = decompose_fee(comp, partition)

    # Compute boundary obligations from decomposition
    own_obligations: tuple = ()
    if decomposition and decomposition.boundary_fee > 0:
        from bulla.model import BoundaryObligation
        own_obligations = boundary_obligations_from_decomposition(
            comp, list(decomposition.partition), diag,
        )

    # Check parent obligations (propagation rule: unmet parent + own new)
    obligation_check: dict | None = None
    propagated_unmet: tuple = ()
    if chain_receipt_data:
        parent_obl_dicts = chain_receipt_data.get("boundary_obligations")
        if parent_obl_dicts:
            from bulla.model import BoundaryObligation
            chain_dims = (
                chain_receipt_data.get("inline_dimensions", {})
                .get("dimensions", {})
            )
            parent_obligations = tuple(
                BoundaryObligation(
                    placeholder_tool=o["placeholder_tool"],
                    dimension=o["dimension"],
                    field=o["field"],
                    source_edge=o.get("source_edge", ""),
                    expected_value=o.get(
                        "expected_value",
                        (chain_dims.get(o["dimension"], {})
                         .get("known_values", [""]))[0],
                    ),
                )
                for o in parent_obl_dicts
            )
            met, unmet, irrelevant = check_obligations(parent_obligations, comp)
            obligation_check = {
                "parent_total": len(parent_obligations),
                "met": len(met),
                "unmet": len(unmet),
                "irrelevant": len(irrelevant),
                "met_obligations": [o.to_dict() for o in met],
                "unmet_obligations": [o.to_dict() for o in unmet],
                "irrelevant_obligations": [o.to_dict() for o in irrelevant],
            }
            propagated_unmet = unmet

    # --guided-discover / --converge: obligation-directed LLM repair
    guided_repair_report: dict | None = None
    do_guided = getattr(args, "guided_discover", False)
    do_converge = getattr(args, "converge", False)
    max_rounds = getattr(args, "max_rounds", 5)
    if (do_guided or do_converge) and (own_obligations or propagated_unmet):
        from bulla.discover.adapter import get_adapter as _get_guided_adapter

        provider = getattr(args, "discover_provider", "auto")
        try:
            guided_adapter = _get_guided_adapter(provider)
        except (ValueError, ImportError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        all_guided_obls: list = []
        seen_g: set[tuple[str, str, str]] = set()
        for obl in (*propagated_unmet, *own_obligations):
            key = (obl.placeholder_tool, obl.dimension, obl.field)
            if key not in seen_g:
                seen_g.add(key)
                all_guided_obls.append(obl)

        if all_guided_obls and decomposition:
            from bulla.infer.classifier import load_pack_stack as _load_ps
            merged_packs, _ = _load_ps(
                extra_paths=(list(getattr(args, "packs", None) or [])
                             + ([inherited_pack_path] if inherited_pack_path else [])
                             + ([discovered_pack_path] if discovered_pack_path else []))
                or None
            )

            if do_converge:
                from bulla.repair import coordination_step as _coordination_step
                conv_result = _coordination_step(
                    comp,
                    list(decomposition.partition),
                    all_tools,
                    guided_adapter,
                    max_rounds=max_rounds,
                    pack_context=merged_packs,
                    parent_obligations=tuple(all_guided_obls),
                )
                conv_pack = conv_result.discovered_pack
                conv_pack_dims = conv_pack.get("dimensions", {})
                guided_repair_report = {
                    "original_fee": diag.coherence_fee,
                    "repaired_fee": conv_result.final_fee,
                    "confirmed": conv_result.total_confirmed,
                    "denied": conv_result.total_denied,
                    "uncertain": conv_result.total_uncertain,
                    "rounds": len(conv_result.rounds),
                    "termination_reason": conv_result.termination_reason,
                    "converged": conv_result.converged,
                    "probes": [
                        p.to_dict()
                        for r in conv_result.rounds
                        for p in r.probes
                    ],
                }
                if conv_pack_dims:
                    guided_repair_report["discovered_pack"] = conv_pack
                    if discovered_pack_data is None:
                        discovered_pack_data = conv_pack
                    else:
                        merged_dims_d = dict(discovered_pack_data.get("dimensions", {}))
                        merged_dims_d.update(conv_pack_dims)
                        discovered_pack_data["dimensions"] = merged_dims_d
                comp = conv_result.final_comp
                diag = diagnose(comp)
                disclosure = prescriptive_disclosure(comp, diag.coherence_fee)
                if len(server_names) > 1:
                    decomposition = decompose_fee(comp, list(decomposition.partition))
                own_obligations = ()
                if decomposition and decomposition.boundary_fee > 0:
                    own_obligations = boundary_obligations_from_decomposition(
                        comp, list(decomposition.partition), diag,
                    )
            else:
                from bulla.discover.engine import guided_discover
                from bulla.repair import extract_pack_from_probes, repair_composition

                guided_result = guided_discover(
                    tuple(all_guided_obls), all_tools, guided_adapter, merged_packs,
                )

                if guided_result.confirmed:
                    repaired_comp = repair_composition(comp, guided_result.confirmed)
                    repaired_diag = diagnose(repaired_comp)

                    single_pack = extract_pack_from_probes(
                        guided_result.probes,
                        comp.canonical_hash()[:8],
                    )
                    single_pack_dims = single_pack.get("dimensions", {})

                    guided_repair_report = {
                        "original_fee": diag.coherence_fee,
                        "repaired_fee": repaired_diag.coherence_fee,
                        "confirmed": guided_result.n_confirmed,
                        "denied": guided_result.n_denied,
                        "uncertain": guided_result.n_uncertain,
                        "probes": [p.to_dict() for p in guided_result.probes],
                    }
                    if single_pack_dims:
                        guided_repair_report["discovered_pack"] = single_pack
                        if discovered_pack_data is None:
                            discovered_pack_data = single_pack
                        else:
                            merged_dims_d = dict(discovered_pack_data.get("dimensions", {}))
                            merged_dims_d.update(single_pack_dims)
                            discovered_pack_data["dimensions"] = merged_dims_d

                    comp = repaired_comp
                    diag = repaired_diag
                    disclosure = prescriptive_disclosure(comp, diag.coherence_fee)
                    if decomposition and len(server_names) > 1:
                        decomposition = decompose_fee(comp, list(decomposition.partition))

                    own_obligations = ()
                    if decomposition and decomposition.boundary_fee > 0:
                        own_obligations = boundary_obligations_from_decomposition(
                            comp, list(decomposition.partition), diag,
                        )
                else:
                    guided_repair_report = {
                        "original_fee": diag.coherence_fee,
                        "repaired_fee": diag.coherence_fee,
                        "confirmed": 0,
                        "denied": guided_result.n_denied,
                        "uncertain": guided_result.n_uncertain,
                        "probes": [p.to_dict() for p in guided_result.probes],
                    }

    # Compute repair geometry + epistemic receipt when fee > 0
    repair_geo = None
    if diag.coherence_fee > 0:
        from bulla.proxy import compute_repair_geometry
        repair_geo = compute_repair_geometry(guard)

    fmt = getattr(args, "format", "text")
    verbose = getattr(args, "verbose", False)

    if manifests_dir:
        from types import SimpleNamespace
        audit_results = []
        for sname in server_names:
            n_tools = sum(1 for t in comp.tools if t.name.startswith(f"{sname}__"))
            audit_results.append(SimpleNamespace(name=sname, ok=True, tools=[None] * n_tools, error=None))
        elapsed = (scan_elapsed_s or 0) + diag_elapsed_s
        context_line = f"manifests · {len(server_names)} servers · {diag.n_tools} tools · {elapsed:.1f}s"
    else:
        audit_results = results
        ctx_parts: list[str] = []
        if chosen_host is not None:
            ctx_parts.append(chosen_host.display_name)
        elif config_path is not None:
            ctx_parts.append(Path(config_path).name)
        else:
            ctx_parts.append("audit")
        ctx_parts.append(f"{len(audit_results)} servers")
        ctx_parts.append(f"{diag.n_tools} tools")
        total_s = (scan_elapsed_s or 0) + diag_elapsed_s
        ctx_parts.append(f"{total_s:.1f}s")
        context_line = " · ".join(ctx_parts)

    _fail_servers = sum(1 for r in audit_results if not r.ok)
    if _fail_servers:
        context_line = f"{context_line} · {_fail_servers} skipped"

    if fmt == "json":
        print(_audit_json(
            audit_results, diag, disclosure, basis, decomposition,
            own_obligations=own_obligations or None,
            obligation_check=obligation_check,
            guided_repair=guided_repair_report,
            repair_geometry=repair_geo,
            raw_tools=all_tools,
            context_line=context_line,
        ))
    elif fmt == "sarif":
        sarif_path = Path(str(config_path)) if config_path else Path("audit.json")
        print(format_sarif([(diag, sarif_path)]))
    else:
        print(_audit_text(
            audit_results, diag, disclosure, basis, decomposition,
            verbose=verbose,
            own_obligations=own_obligations or None,
            obligation_check=obligation_check,
            guided_repair=guided_repair_report,
            repair_geometry=repair_geo,
            raw_tools=all_tools,
            context_line=context_line,
        ))
        if discovery_n_dims > 0:
            print(f"  Discovery: {discovery_n_dims} dimension(s) added to vocabulary", file=sys.stderr)

    output_comp = getattr(args, "output_composition", None)
    if output_comp:
        guard.to_yaml(output_comp)
        print(f"Wrote composition to {output_comp}", file=sys.stderr)

    # Combine: propagated unmet from parent + own new obligations (deduplicated)
    combined_obligations: tuple["BoundaryObligation", ...] | None = None
    if propagated_unmet or own_obligations:
        from bulla.model import BoundaryObligation
        seen_keys: dict[tuple[str, str, str], BoundaryObligation] = {}
        for obl in (*propagated_unmet, *own_obligations):
            key = (obl.placeholder_tool, obl.dimension, obl.field)
            if key not in seen_keys:
                seen_keys[key] = obl
        combined_obligations = tuple(seen_keys.values()) if seen_keys else None

    # Compute counts for policy enforcement
    _unmet_count = obligation_check["unmet"] if obligation_check else 0
    _contradiction_count = 0
    if guided_repair_report:
        disc_pack = guided_repair_report.get("discovered_pack")
        if disc_pack:
            from bulla.repair import detect_contradictions as _dc
            _contradiction_count = len(_dc(disc_pack))

    _struct_diag = guard.structural_diagnostic
    _structural_score = (
        _struct_diag.contradiction_score if _struct_diag is not None else 0
    )

    # --receipt: produce WitnessReceipt
    if receipt_path:
        from bulla.witness import witness

        parent_hash = None
        if chain_receipt_data:
            parent_hash = chain_receipt_data.get("receipt_hash")

        inline_dims = None
        chain_inline = (
            chain_receipt_data.get("inline_dimensions")
            if chain_receipt_data else None
        )
        if chain_inline and isinstance(chain_inline, dict) and discovered_pack_data:
            import copy as _copy
            inline_dims = _copy.deepcopy(chain_inline)
            inherited_dims = inline_dims.get("dimensions", {})
            new_dims = discovered_pack_data.get("dimensions", {})
            inherited_dims.update(new_dims)
            inline_dims["dimensions"] = inherited_dims
        elif discovered_pack_data:
            inline_dims = discovered_pack_data
        elif chain_inline and isinstance(chain_inline, dict):
            inline_dims = chain_inline

        receipt_contradictions = None
        if inline_dims:
            from bulla.repair import detect_contradictions
            _cr = detect_contradictions(inline_dims)
            if _cr:
                receipt_contradictions = _cr
                _contradiction_count = len(_cr)

        _struct_contras = (
            _struct_diag.contradictions
            if _struct_diag is not None and _struct_diag.contradictions
            else None
        )
        receipt = witness(
            diag, comp,
            witness_basis=basis,
            active_packs=get_active_pack_refs(),
            parent_receipt_hash=parent_hash,
            inline_dimensions=inline_dims,
            boundary_obligations=combined_obligations,
            contradictions=receipt_contradictions,
            unmet_obligations=_unmet_count,
            contradiction_count=_contradiction_count,
            structural_contradictions=_struct_contras,
            contradiction_score=_structural_score,
        )
        receipt_dict = receipt.to_dict()
        receipt_path.write_text(
            json.dumps(receipt_dict, indent=2), encoding="utf-8"
        )
        print(f"  Receipt written to {receipt_path}", file=sys.stderr)

    # Cleanup temp files
    if inherited_pack_path:
        inherited_pack_path.unlink(missing_ok=True)
    if discovered_pack_path:
        discovered_pack_path.unlink(missing_ok=True)

    _gauge_threshold_check(
        diag, args,
        unmet_count=_unmet_count,
        contradiction_count=_contradiction_count,
        structural_contradiction_score=_structural_score,
    )


# ── discover ──────────────────────────────────────────────────────────


def _cmd_discover(args: argparse.Namespace) -> None:
    """Discover convention dimensions from tool schemas using an LLM."""
    _configure_packs_from_args(args)
    from bulla.discover.engine import discover_dimensions

    manifests_dir: Path = args.manifests
    if not manifests_dir.is_dir():
        print(f"Error: {manifests_dir} is not a directory.", file=sys.stderr)
        sys.exit(1)

    all_tools, server_names = _load_manifests_dir(manifests_dir)
    if not all_tools:
        print("Error: No tools found in manifest directory.", file=sys.stderr)
        sys.exit(1)

    provider = getattr(args, "provider", "auto")
    adapter = None
    if provider != "auto":
        from bulla.discover.adapter import get_adapter
        adapter = get_adapter(provider)

    try:
        result = discover_dimensions(
            all_tools,
            adapter=adapter,
            existing_packs=getattr(args, "packs", None),
            session_id=None,
        )
    except (ValueError, ImportError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    output_path: Path = args.output
    if result.valid and result.n_dimensions > 0:
        output_path.write_text(
            yaml.dump(result.pack, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        print(f"  Discovered {result.n_dimensions} dimension(s) across "
              f"{len(server_names)} server(s)", file=sys.stderr)
        print(f"  Wrote micro-pack to {output_path}", file=sys.stderr)

        dims = result.pack.get("dimensions", {})
        for dim_name, dim_def in dims.items():
            desc = dim_def.get("description", "")[:60]
            refines = dim_def.get("refines")
            ref_str = f" (refines {refines})" if refines else ""
            print(f"    - {dim_name}: {desc}{ref_str}", file=sys.stderr)
    elif result.valid and result.n_dimensions == 0:
        print("  No new dimensions discovered. Tool schemas may be too sparse "
              "for the current prompt.", file=sys.stderr)
    else:
        print("  Discovery produced invalid output:", file=sys.stderr)
        for err in result.errors:
            print(f"    [error] {err}", file=sys.stderr)
        sys.exit(1)

    raw_path = output_path.with_suffix(".raw.txt")
    raw_path.write_text(result.raw_response, encoding="utf-8")
    print(f"  Raw LLM response saved to {raw_path}", file=sys.stderr)


# ── merge ─────────────────────────────────────────────────────────────


def _cmd_merge(args: argparse.Namespace) -> None:
    """Merge vocabularies from multiple receipts (DAG support)."""
    from bulla.merge import merge_receipt_obligations, merge_receipt_vocabularies

    receipt_paths: list[Path] = args.receipts
    receipt_dicts: list[dict] = []
    receipt_names: list[str] = []
    for rp in receipt_paths:
        if not rp.exists():
            print(f"Error: receipt not found: {rp}", file=sys.stderr)
            sys.exit(1)
        receipt_dicts.append(json.loads(rp.read_text(encoding="utf-8")))
        receipt_names.append(rp.name)

    merged_vocab, overlaps = merge_receipt_vocabularies(receipt_dicts)

    if merged_vocab is None:
        print("  No inline_dimensions found in any receipt.", file=sys.stderr)
        sys.exit(1)

    merged_dims = merged_vocab.get("dimensions", {})
    per_receipt_counts: list[int] = []
    for rd in receipt_dicts:
        inline = rd.get("inline_dimensions", {})
        per_receipt_counts.append(len(inline.get("dimensions", {})) if inline else 0)

    merged_obls = merge_receipt_obligations(receipt_dicts)

    fmt = getattr(args, "format", "text")
    receipt_path: Path | None = getattr(args, "receipt", None)

    if fmt == "json":
        obj: dict = {
            "receipts": len(receipt_dicts),
            "merged_dimensions": len(merged_dims),
            "per_receipt_counts": per_receipt_counts,
            "overlaps": [
                {
                    "dim_a": o.dim_a,
                    "receipt_a": receipt_names[o.receipt_a_idx],
                    "dim_b": o.dim_b,
                    "receipt_b": receipt_names[o.receipt_b_idx],
                    "shared_patterns": list(o.shared_patterns),
                }
                for o in overlaps
            ],
            "dimensions": list(merged_dims.keys()),
        }
        if merged_obls:
            obj["boundary_obligations"] = [o.to_dict() for o in merged_obls]
        print(json.dumps(obj, indent=2))
    else:
        counts_str = ", ".join(
            f"{c} from {n}" for c, n in zip(per_receipt_counts, receipt_names)
        )
        print(
            f"  bulla merge: {len(receipt_dicts)} receipts, "
            f"{len(merged_dims)} dimensions merged ({counts_str}, "
            f"{len(overlaps)} overlapping)"
        )
        print()

        if overlaps:
            print("  Overlap:")
            for o in overlaps:
                pats = ", ".join(o.shared_patterns)
                print(
                    f"    {o.dim_a} ({receipt_names[o.receipt_a_idx]}) <-> "
                    f"{o.dim_b} ({receipt_names[o.receipt_b_idx]}): "
                    f"field_patterns intersect on {pats}"
                )
            print()

        print(f"  Merged vocabulary: {len(merged_dims)} unique dimensions")

        if merged_obls:
            print(f"  Accumulated obligations: {len(merged_obls)}")
            for obl in merged_obls:
                print(
                    f"    - {obl.dimension}: field \"{obl.field}\" "
                    f"in {obl.placeholder_tool} group"
                )

    if receipt_path:
        from bulla.model import Disposition, DEFAULT_POLICY_PROFILE
        from bulla import __version__ as kver
        from datetime import datetime, timezone
        from bulla.model import WitnessReceipt

        parent_hashes = tuple(
            rd["receipt_hash"] for rd in receipt_dicts if "receipt_hash" in rd
        )

        merge_receipt = WitnessReceipt(
            receipt_version="0.1.0",
            kernel_version=kver,
            composition_hash="no_composition",
            diagnostic_hash="no_diagnostic",
            policy_profile=DEFAULT_POLICY_PROFILE,
            fee=0,
            blind_spots_count=0,
            bridges_required=0,
            unknown_dimensions=0,
            disposition=Disposition.PROCEED,
            timestamp=datetime.now(timezone.utc).isoformat(),
            parent_receipt_hashes=parent_hashes if parent_hashes else None,
            inline_dimensions=merged_vocab,
            boundary_obligations=merged_obls,
        )
        receipt_dict = merge_receipt.to_dict()
        receipt_path.write_text(
            json.dumps(receipt_dict, indent=2), encoding="utf-8"
        )
        print(f"  Receipt written to {receipt_path}", file=sys.stderr)


# ── pack ──────────────────────────────────────────────────────────────


def _cmd_pack_validate(args: argparse.Namespace) -> None:
    """Validate a convention pack YAML file."""
    from bulla.packs.validate import validate_pack

    path = args.file
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    errors = validate_pack(parsed)

    if not errors:
        print(f"  VALID  {path}")
    else:
        for err in errors:
            print(f"  [error] {err}")
        sys.exit(1)


def _cmd_pack_verify(args: argparse.Namespace) -> None:
    """Inspect a pack's values_registry pointers and check status.

    By default this is a *static* inspection (Extension B core): it
    walks the pack, lists the registry references, identifies which
    ones would require a license credential, and reports without
    fetching. Pass ``--fetch`` to actually fetch and hash-check (HTTP
    fetch implementation is a stub for now; the static inspection is
    fully functional).

    Exit codes:
      0 = all pointers OK or all gated on missing credentials (static OK)
      1 = pack failed schema validation, or any pointer hash-mismatched,
          or any registry was unavailable when --fetch was requested
    """
    from bulla.packs.validate import validate_pack
    from bulla.packs.verify import (
        CredentialProvider,
        inspect_registries,
        verify_pack_registries,
    )

    path = args.file
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    errors = validate_pack(parsed)
    if errors:
        for err in errors:
            print(f"  [validation error] {err}", file=sys.stderr)
        sys.exit(1)

    refs = inspect_registries(parsed)
    if not refs:
        print(f"  NO REGISTRIES  {path} (no values_registry pointers found)")
        return

    print(f"  {len(refs)} registry pointer(s) in {parsed.get('pack_name', path.name)}:")
    for ref in refs:
        gate = (
            "open"
            if ref.registry_license == "open"
            else f"requires license_id={ref.license_id!r}"
        )
        # Distinguish placeholder hashes from real sha256 — placeholder
        # is "structurally ready, not yet ingested," not "verified."
        if ref.expected_hash.startswith("placeholder:"):
            hash_display = (
                f"\033[33mPLACEHOLDER\033[0m "
                f"({ref.expected_hash[len('placeholder:'):]})"
            )
        else:
            hash_display = f"hash={ref.expected_hash[:16]}…"
        print(
            f"    - {ref.dimension}: {ref.uri}  "
            f"version={ref.version}  {hash_display}  "
            f"({gate})"
        )

    if not getattr(args, "fetch", False):
        return

    # Real fetch: not implemented yet; the production HTTP fetcher
    # lands when Phase 2/3 packs actually exist to verify against.
    # For now, --fetch is a stub that surfaces the design clearly.
    print(
        "  [info] --fetch requested but the HTTP registry fetcher is "
        "not yet wired up. Static inspection above is the current "
        "production behavior (Phase 1 of the Standards Ingestion sprint).",
        file=sys.stderr,
    )

    # When the HTTP fetcher lands, the call shape is:
    #   results = verify_pack_registries(
    #       parsed, fetcher=HttpFetcher(),
    #       credential_provider=CredentialProvider(load_credentials_from_env()),
    #   )
    # and exit nonzero on any "hash_mismatch" / "unavailable" / "license_required"


def _cmd_pack_status(args: argparse.Namespace) -> None:
    """Surface a pack's metadata: license, derives_from, registry refs.

    Read-only inspection useful for humans and CI gates. No network.
    """
    from bulla.packs.validate import validate_pack
    from bulla.packs.verify import inspect_registries

    path = args.file
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    errors = validate_pack(parsed)
    if errors:
        for err in errors:
            print(f"  [validation error] {err}", file=sys.stderr)
        sys.exit(1)

    print(f"  pack:            {parsed.get('pack_name', '?')}")
    print(f"  pack_version:    {parsed.get('pack_version', '?')}")
    print(f"  dimensions:      {len(parsed.get('dimensions', {}))}")

    derives = parsed.get("derives_from") or {}
    if derives:
        print(
            f"  derives_from:    {derives.get('standard', '?')} "
            f"version={derives.get('version', '?')}"
        )
        if derives.get("source_uri"):
            print(f"  derives.source:  {derives['source_uri']}")

    license_block = parsed.get("license") or {}
    if license_block:
        print(
            f"  license.spdx:    {license_block.get('spdx_id', '(unset)')}"
        )
        print(
            f"  license.regreg:  "
            f"{license_block.get('registry_license', '(unset)')}"
        )
        if license_block.get("source_url"):
            print(f"  license.source:  {license_block['source_url']}")
        if license_block.get("attribution"):
            print(f"  license.attrib:  {license_block['attribution']}")
    else:
        print("  license:         (no license block)")

    refs = inspect_registries(parsed)
    if refs:
        print(f"  registries:      {len(refs)} pointer(s)")
        for ref in refs:
            print(
                f"    - {ref.dimension}  "
                f"version={ref.version}  license_id={ref.license_id or '(none)'}"
            )
    else:
        print("  registries:      (none — inline known_values only)")

    from bulla.mappings import list_mappings

    map_summary = list_mappings(parsed)
    if map_summary:
        total = sum(n for _, _, n in map_summary)
        print(
            f"  mappings:        {len(map_summary)} table(s), "
            f"{total} row(s) total"
        )
        for target_pack, target_dim, n in map_summary:
            print(
                f"    - {target_pack}.{target_dim}: {n} row(s)"
            )


def _cmd_pack_lint(args: argparse.Namespace) -> None:
    """Lint a pack: surface non-fatal style issues and upgrade hints.

    Validation must already pass. Lint findings are advisory; this
    command never exits non-zero on a finding alone unless ``--strict``
    is passed.
    """
    from bulla.packs.validate import validate_pack

    path = args.file
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    errors = validate_pack(parsed)
    if errors:
        for err in errors:
            print(f"  [validation error] {err}", file=sys.stderr)
        sys.exit(1)

    findings: list[str] = []

    if "license" not in parsed:
        findings.append(
            "no license block — recommended for any pack that ships "
            "via PyPI or a public registry; describe the underlying "
            "registry's license posture even when it is 'open'"
        )

    dims = parsed.get("dimensions", {})
    if isinstance(dims, dict):
        for dim_name, dim_def in dims.items():
            if not isinstance(dim_def, dict):
                continue
            kv = dim_def.get("known_values")
            if isinstance(kv, list) and len(kv) > 5000:
                findings.append(
                    f"dimensions.{dim_name}: {len(kv)} inline values — "
                    "consider migrating to values_registry to keep pack "
                    "diffs reviewable and pack hashes stable across "
                    "minor curation"
                )

    if not findings:
        print(f"  CLEAN  {path}")
        return

    for f in findings:
        print(f"  [hint] {f}")
    if getattr(args, "strict", False):
        sys.exit(1)


_PAIRWISE_SCAN_CUTOFF = 8
"""When more than this many servers are scanned, skip the pairwise
comparison block. n*(n-1)/2 compose_multi calls dominates wall-clock
time at 8+ servers; the moat case stays visible from the global
diagnostic alone."""


def _no_config_found_message() -> str:
    """Helpful error when bulla scan can't auto-detect any host.

    Distinguishes "no host config found anywhere" from "host config
    found but empty". The latter case is actionable: the user has
    Claude Code or Cursor installed but no MCP servers configured.
    """
    from bulla.hosts import all_hosts

    seen_paths: list[tuple[str, str]] = []
    empty_hosts: list[str] = []
    for host in all_hosts():
        for p in host.candidate_paths():
            if p.exists():
                seen_paths.append((host.display_name, str(p)))
                # Try parsing — if it succeeds with zero servers,
                # mark the host as "configured but empty."
                try:
                    entries = host.parse(p)
                    if not entries:
                        empty_hosts.append(host.display_name)
                except Exception:
                    pass
                break

    lines: list[str] = ["No MCP servers found via auto-detect."]
    lines.append("")
    if empty_hosts:
        lines.append(
            "Detected host configs without MCP servers configured:"
        )
        for name in empty_hosts:
            lines.append(f"  - {name}")
        lines.append("")
        lines.append(
            "Add a server to your host's MCP config (e.g. via "
            "`claude mcp add` for Claude Code, or by editing "
            "~/.cursor/mcp.json for Cursor) and re-run bulla scan."
        )
        lines.append("")
    elif seen_paths:
        lines.append("Files found but unrecognized as MCP configs:")
        for name, path in seen_paths:
            lines.append(f"  - {name}: {path}")
        lines.append("")
    else:
        lines.append("bulla scan checked the standard locations:")
        lines.append("  - Claude Code:    ~/.claude.json, <cwd>/.mcp.json")
        lines.append("  - Cursor:         ~/.cursor/mcp.json")
        lines.append(
            "  - Claude Desktop: "
            "~/Library/Application Support/Claude/claude_desktop_config.json"
        )
        lines.append("  - Cline, Windsurf, Zed, Codex "
                     "(see `bulla hosts list`)")
        lines.append("")
    lines.append("Or pass an explicit target:")
    lines.append("  bulla scan --config /path/to/mcp.json")
    lines.append(
        "  bulla scan \"npx -y @modelcontextprotocol/server-filesystem /tmp\""
    )
    return "\n".join(lines) + "\n"


def _cmd_certify_cost(args: argparse.Namespace) -> None:
    """Coherence Cost Certificate v0 — see bulla.certify_cost."""
    import json as _json

    from bulla.certify_cost import build_certificate
    from bulla.parser import load_composition

    comp = load_composition(args.composition)
    print(_json.dumps(build_certificate(comp, args.observed_cost), indent=2))


def _cmd_scan(args: argparse.Namespace) -> None:
    _configure_packs_from_args(args)

    server_tools, config_source = _resolve_scan_targets(args)
    if server_tools is None:
        return  # _resolve_scan_targets already exited or printed the error

    # Build a BullaGuard for legacy formats (text / sarif / -o yaml)
    # AND keep server_tools as the source of truth for narrative +
    # pairwise. Both paths share the same diagnosis underneath
    # (compose_multi internally builds a Composition that matches what
    # BullaGuard.from_tools_list would produce on the same flat list).
    flat_tools, naming = _flatten_for_guard(server_tools)
    from bulla.guard import BullaGuard
    guard = BullaGuard.from_tools_list(
        flat_tools, name=naming
    )
    diag = guard.diagnose()

    fmt = getattr(args, "format", "narrative")
    if getattr(args, "json", False):
        fmt = "json"

    if fmt == "json":
        print(guard.to_json())
    elif fmt == "sarif":
        print(guard.to_sarif())
    elif fmt == "text":
        # The legacy mathematician-grade output; preserved for power
        # users who want β₁, H¹, and δ₀ rank in the receipt.
        print(guard.to_text())
    else:
        # narrative — the awareness-gap-fix default.
        _render_scan_narrative(args, diag, server_tools, config_source)

    if args.output:
        guard.to_yaml(args.output)
        print(f"Wrote composition to {args.output}", file=sys.stderr)


def _resolve_scan_targets(
    args: argparse.Namespace,
) -> tuple[dict[str, list[dict]] | None, str | None]:
    """Resolve the scan inputs to ``(server_tools, config_source)``.

    ``server_tools`` is a dict of ``{server_name: [tool_dict, ...]}``
    suitable for both ``BullaGuard.from_tools_list`` (after flattening)
    and ``compute_pairwise_fees``. ``config_source`` is a human-readable
    label for the narrative header (e.g. ``"~/.cursor/mcp.json"``).

    Returns ``(None, None)`` and exits the process on terminal failure
    (no config found, host parser raised, etc.).
    """
    from bulla.scan import ScanError, scan_mcp_server

    commands = list(args.commands or [])
    config_path = getattr(args, "config", None)

    # Branch A: explicit positional commands.
    if commands:
        return _scan_explicit_commands(commands)

    # Branch B: --config <path>.
    if config_path:
        from bulla.config import parse_mcp_config
        try:
            entries = parse_mcp_config(config_path)
        except Exception as e:
            print(f"Error reading {config_path}: {e}", file=sys.stderr)
            sys.exit(1)
        if not entries:
            print(
                f"No MCP servers configured in {config_path}.",
                file=sys.stderr,
            )
            sys.exit(1)
        return _scan_named_entries(entries, str(config_path))

    # Branch C: auto-detect via registered hosts.
    from bulla.hosts import detect_all
    matches = detect_all()
    if not matches:
        print(_no_config_found_message(), file=sys.stderr)
        sys.exit(1)
    chosen = matches[0]
    try:
        entries = chosen.host.parse(chosen.path)
    except Exception as e:
        print(
            f"Error parsing {chosen.host.display_name} config "
            f"at {chosen.path}: {e}",
            file=sys.stderr,
        )
        sys.exit(1)
    if not entries:
        print(
            f"No MCP servers configured in {chosen.path}.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
        f"Auto-detected: {chosen.host.display_name} ({chosen.path})",
        file=sys.stderr,
    )
    return _scan_named_entries(entries, str(chosen.path))


def _scan_explicit_commands(
    commands: list[str],
) -> tuple[dict[str, list[dict]], str | None]:
    """Scan each explicit shell command, naming each server by the
    last path segment of its command (e.g.
    ``server-filesystem`` from ``npx -y @mcp/server-filesystem``)."""
    from bulla.scan import ScanError, scan_mcp_server

    server_tools: dict[str, list[dict]] = {}
    used_names: set[str] = set()
    for cmd in commands:
        try:
            tools = scan_mcp_server(cmd)
        except ScanError as e:
            print(f"Error scanning {cmd!r}: {e}", file=sys.stderr)
            sys.exit(1)
        name = _server_name_from_command(cmd, used_names)
        used_names.add(name)
        server_tools[name] = tools
    config_source = commands[0] if len(commands) == 1 else None
    return server_tools, config_source


def _scan_named_entries(
    entries: list, config_source: str,
) -> tuple[dict[str, list[dict]], str]:
    """Scan a list of ``McpServerEntry`` objects (already named)."""
    from bulla.scan import ScanError, scan_mcp_server

    server_tools: dict[str, list[dict]] = {}
    for entry in entries:
        try:
            tools = scan_mcp_server(entry.command, env=entry.env)
        except ScanError as e:
            print(
                f"Error scanning server {entry.name!r}: {e}",
                file=sys.stderr,
            )
            sys.exit(1)
        server_tools[entry.name] = tools
    return server_tools, config_source


def _server_name_from_command(cmd: str, used: set[str]) -> str:
    """Derive a stable, unique server name from a shell command.

    Heuristic: scan the command's tokens left-to-right, picking the
    last one that looks like a package identifier
    (contains ``-`` or starts with ``@``, doesn't look like a flag
    or filesystem path argument). For
    ``npx -y @modelcontextprotocol/server-filesystem /tmp`` this
    yields ``server-filesystem``; the trailing ``/tmp`` is correctly
    skipped as a path argument.

    Falls back to the last non-flag token, then to ``"server"``.
    Append ``-2``, ``-3`` suffixes when the name collides.
    """
    parts = cmd.split()
    candidate: str | None = None
    for tok in parts:
        if tok.startswith("-"):
            continue  # flags
        if tok.startswith("/"):
            continue  # filesystem paths
        # Strip leading @scope/ for matching purposes; keep the
        # remainder as a candidate.
        stripped = tok.split("/")[-1].removesuffix(".py")
        if not stripped:
            continue
        # Looks like a package name when it has a hyphen, or the
        # original token had an @scope/ prefix, or it's a known
        # MCP-server-shaped identifier.
        if "-" in stripped or "@" in tok:
            candidate = stripped
    if candidate is None:
        # Fall back to the last non-flag, non-path token.
        for tok in reversed(parts):
            if not tok.startswith("-") and not tok.startswith("/"):
                candidate = tok.split("/")[-1].removesuffix(".py")
                if candidate:
                    break
    if not candidate:
        candidate = "server"
    name = candidate
    suffix = 2
    while name in used:
        name = f"{candidate}-{suffix}"
        suffix += 1
    return name


def _flatten_for_guard(
    server_tools: dict[str, list[dict]],
) -> tuple[list[dict], str]:
    """Flatten ``{server: [tool, ...]}`` to a single list with
    ``server__tool`` namespacing, matching what
    ``compose_multi`` produces internally."""
    flat: list[dict] = []
    for server, tools in server_tools.items():
        for tool in tools:
            tool_copy = dict(tool)
            tool_copy["name"] = f"{server}__{tool['name']}"
            flat.append(tool_copy)
    if len(server_tools) == 1:
        only = next(iter(server_tools.keys()))
        return flat, f"scan-{only}"
    return flat, "multi-server-scan"


def _render_scan_narrative(
    args: argparse.Namespace,
    diag,
    server_tools: dict[str, list[dict]],
    config_source: str | None,
) -> None:
    """Render the narrative output. Pairwise comparison fires only on
    the moat case (every pair fee=0, global fee>0); the dict of
    server tools we already have is the input — no re-flattening, no
    empty-schema fakes."""
    from bulla.scan_format import (
        compute_pairwise_fees,
        format_scan_narrative,
    )

    server_names = sorted(server_tools.keys())
    pairwise: dict[tuple[str, str], int] | None = None
    if (
        len(server_names) >= 2
        and len(server_names) <= _PAIRWISE_SCAN_CUTOFF
        and not getattr(args, "no_pairwise", False)
    ):
        try:
            pairwise = compute_pairwise_fees(server_tools)
        except Exception as e:
            # Surface to stderr — silently dropping the pairwise block
            # would hide the moat case on the exact runs where it
            # most matters. The user can pass --no-pairwise to suppress
            # the warning explicitly.
            print(
                f"warning: pairwise comparison skipped ({type(e).__name__}: {e}); "
                "pass --no-pairwise to silence.",
                file=sys.stderr,
            )
            pairwise = None

    narrative = format_scan_narrative(
        diag,
        server_names,
        config_source=config_source,
        pairwise_fees=pairwise,
    )
    print(narrative, end="")


def _cmd_receipt(args: argparse.Namespace) -> None:
    """`bulla receipt` with no subcommand — point at `create` / `verify`."""
    if not getattr(args, "receipt_command", None):
        print("usage: bulla receipt create --type <act> [--subject k=v ...] --forum-endpoint URL --forum-root REF")
        print("       bulla receipt verify <file.json>")
        print("  create: mint an ActionReceipt for one consequential action (sign with --key).")
        print("  verify: recompute the hashes, the recourse envelope (modality law), the")
        print("          signature, convention conformance — honest about depth.")
        sys.exit(2)


def _parse_kv_value(raw: str):
    """`k=v` values: JSON scalar when it parses (1250 -> int, true -> bool),
    else the raw string — so quantum/conformance checks see real types."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _cmd_receipt_create(args: argparse.Namespace) -> None:
    """Mint an ActionReceipt from flags — the stranger's ergonomic: one
    command from act to signed, verifiable receipt, no Python required."""
    from bulla.action_receipt import build_action_receipt, sign_action_receipt
    from bulla.envelope import (
        Authority, Bounds, EnvelopeError, Forum, Recourse, RecourseEnvelope, Remedy,
    )

    subject: dict = {}
    if getattr(args, "subject_json", None):
        subject.update(json.loads(Path(args.subject_json).read_text()))
    for kv in args.subject or []:
        if "=" not in kv:
            print(f"Error: --subject expects k=v, got {kv!r}", file=sys.stderr)
            sys.exit(2)
        k, v = kv.split("=", 1)
        subject[k] = _parse_kv_value(v)

    if args.diagnostic_ref:
        diagnostic_ref = {"status": "reference", "ref": args.diagnostic_ref}
    else:
        diagnostic_ref = {"status": args.diagnostic_status}

    evidence: list[dict] = []
    for spec_ in args.evidence or []:
        parts = spec_.split("=", 1)
        if len(parts) != 2:
            print(f"Error: --evidence expects name=hash[:grounding], got {spec_!r}", file=sys.stderr)
            sys.exit(2)
        name, rest = parts
        grounding = "self_asserted"
        h = rest
        for g in ("self_asserted", "counterparty_signed", "third_party_anchored", "execution_verified"):
            if rest.endswith(":" + g):
                h, grounding = rest[: -len(g) - 1], g
                break
        evidence.append({"name": name, "hash": h, "grounding": grounding})

    conventions: list[dict] = []
    for cpath in args.convention or []:
        conventions.append(json.loads(Path(cpath).read_text()))

    remedies: list[Remedy] = []
    for rspec in args.remedy or ["recompute:bulla receipt verify:hashes.content"]:
        bits = rspec.split(":", 2)
        if len(bits) != 3:
            print(f"Error: --remedy expects rung:verifier:anchor, got {rspec!r}", file=sys.stderr)
            sys.exit(2)
        remedies.append(Remedy(rung=bits[0], verifier=bits[1], anchor=bits[2]))

    anchor_ref: dict = {}
    if args.anchor:
        if "=" not in args.anchor:
            print(f"Error: --anchor expects kind=ref, got {args.anchor!r}", file=sys.stderr)
            sys.exit(2)
        kind_, ref_ = args.anchor.split("=", 1)
        anchor_ref = {"kind": kind_, "ref": ref_}

    try:
        envelope = RecourseEnvelope(
            authority=(
                Authority(principal=args.principal, policy=args.policy or "policy://unstated")
                if args.principal else None
            ),
            bounds=Bounds(scope=args.scope) if args.scope else None,
            recourse=Recourse(
                challenge_window=args.challenge_window,
                forum=Forum(log_endpoint=args.forum_endpoint, trusted_root_ref=args.forum_root),
                remedies=tuple(remedies),
            ),
            retention_class=args.retention,
            disclosure_class=args.disclosure,
        )
    except EnvelopeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

    from datetime import datetime, timezone
    timestamp = args.timestamp or datetime.now(timezone.utc).isoformat()
    producer = {"bulla_version": __version__}

    kwargs = dict(
        action={"type": args.type, "subject": subject},
        diagnostic_ref=diagnostic_ref,
        envelope=envelope,
        anchor_ref=anchor_ref,
        evidence_refs=tuple(evidence),
        conventions=tuple(conventions),
        timestamp=timestamp,
        producer=producer,
    )
    try:
        receipt = build_action_receipt(**kwargs)
        if getattr(args, "key", None):
            signer = _load_signer_or_exit(args.key, getattr(args, "issuer", None))
            receipt = sign_action_receipt(receipt, signer)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

    out = receipt.to_json()
    if getattr(args, "out", None):
        Path(args.out).write_text(out + "\n")
        signed = "signed" if receipt.signature else "unsigned"
        print(f"receipt       {args.out} ({signed})  content={receipt.content_hash}")
    else:
        print(out)


def _cmd_receipt_verify(args: argparse.Namespace) -> None:
    """One verifier over the tagged union {action_receipt, witness_receipt,
    certificate}. Dispatches on ``kind``, reports the ``verified_to`` depth, and
    fails closed. Exit 0 iff the receipt verifies; 1 on a verification failure;
    2 on unreadable input or an unknown kind."""
    import base64

    try:
        doc = json.loads(Path(args.receipt).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"✗ cannot read receipt: {exc}")
        sys.exit(2)

    pub = None
    if getattr(args, "key", None):
        kd = json.loads(Path(args.key).read_text())
        if isinstance(kd, dict) and kd.get("public_key_b64"):
            pub = base64.b64decode(kd["public_key_b64"])

    kind = doc.get("kind")
    if kind == "action_receipt":
        from bulla.action_receipt import verify_receipt

        res = verify_receipt(doc, public_key=pub)
        payload = {
            "kind": kind, "ok": res.ok, "verified_to": res.verified_to,
            "authority_authentic": res.authority_authentic,
            "checks": res.checks, "reasons": list(res.reasons),
        }
        # Preserve the verifier's existing independent dimensions at the CLI
        # boundary. They are not folded into a new boolean or reliance policy.
        for dimension in (
            "chain_integrity", "principal_binding", "policy_binding",
            "scope_binding", "temporal_status", "revocation_status",
            "bounds_conformance",
        ):
            payload[dimension] = getattr(res, dimension)
        if res.effective_grounding is not None:
            payload["effective_grounding"] = res.effective_grounding
        if res.conventions:
            payload["conventions"] = res.conventions
        remedy = doc.get("remedy") if isinstance(doc.get("remedy"), dict) else {}
        forum = remedy.get("forum") if isinstance(remedy.get("forum"), dict) else {}
        content_signature = res.checks.get("signature")
        payload["answerability"] = {
            "integrity": "VERIFIED" if res.ok else "INVALID",
            "authenticity": (
                "VERIFIED" if content_signature is True
                else "INVALID" if content_signature is False
                else "UNVERIFIED"
            ),
            "authority": res.authority_authentic.upper(),
            "scope": (
                res.bounds_conformance.upper()
                if res.bounds_conformance != "not_applicable"
                else res.scope_binding.upper()
            ),
            "grounding": (res.effective_grounding or "UNRESOLVED").upper(),
            "recourse": "NAMED" if forum.get("log_endpoint") else "UNRESOLVED",
            "reachability": "UNVERIFIED",
            "reliance_decision": "NOT_COMPUTED",
        }
    elif kind == "certificate" or "certificate_content_hash" in doc:
        from bulla.certificate import verify_certificate_integrity

        integ = verify_certificate_integrity(doc)
        checks: dict = {"integrity": integ}
        reasons: list[str] = [] if integ else ["certificate content-hash mismatch"]
        vt = "digest" if integ else "none"
        sig = doc.get("signature")
        if integ and sig:
            from bulla.identity import verify_proof

            a = verify_proof(doc.get("certificate_content_hash", ""), sig, public_key=pub)
            checks["signature"] = a.authentic
            vt = "attestation" if a.authentic else "digest"
            if not a.authentic:
                reasons.append(f"signature not authentic ({a.method})")
        ok = integ and checks.get("signature", True)
        payload = {"kind": "certificate", "ok": ok, "verified_to": vt, "checks": checks, "reasons": reasons}
    elif kind == "witness_receipt" or ("receipt_version" in doc and "composition_hash" in doc):
        from bulla.witness import receipt_integrity_report

        rep = receipt_integrity_report(doc)
        ok = rep["ok"]
        reasons = [] if ok else ["receipt_hash mismatch (neither canon-2 nor legacy canon-1 form)"]
        if ok and rep["canon"] == 1:
            reasons.append(
                "legacy canonicalization (CANON_VERSION 1, spaced) — a format "
                "change is a version difference, not tampering"
            )
        payload = {
            "kind": "witness_receipt", "ok": ok,
            "verified_to": "digest" if ok else "none",
            "checks": {"integrity": ok},
            "canon_version": rep["canon"],
            "reasons": reasons,
        }
    else:
        print(f"✗ unknown receipt kind {kind!r} — expected action_receipt / witness_receipt / certificate")
        sys.exit(2)

    if getattr(args, "format", "text") == "json":
        print(json.dumps(payload, indent=2))
    else:
        mark = "✓" if payload["ok"] else "✗"
        authority = (
            f"  authority={payload['authority_authentic']}"
            if "authority_authentic" in payload else ""
        )
        print(f"{mark} {payload['kind']}  verified_to={payload['verified_to']}{authority}")
        for name, val in payload["checks"].items():
            print(f"    {'✓' if val else '✗'} {name}")
        if payload.get("effective_grounding"):
            print(f"    grounding  {payload['effective_grounding']} (minimum over carried evidence)")
        for cname, status in (payload.get("conventions") or {}).items():
            cm = {"conforms": "✓", "violates": "✗", "pinned": "·"}[status]
            print(f"    {cm} convention {cname}: {status}")
        for r in payload["reasons"]:
            print(f"    · {r}")
    sys.exit(0 if payload["ok"] else 1)


def _cmd_coverage(args: argparse.Namespace) -> None:
    """Receipt coverage against PyPI (primary) or strict-SemVer Git tags."""
    from bulla.coverage import (
        coverage_headline,
        git_coverage,
        load_pypi_project,
        pypi_coverage,
    )

    if args.anchor == "pypi":
        project_doc = load_pypi_project(args.snapshot) if args.snapshot else None
        try:
            rep = pypi_coverage(
                str(args.receipts),
                project=args.project,
                project_doc=project_doc,
                verify_integrity=not args.no_integrity,
                expected_repository=args.expected_repository,
            )
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(2)
    else:
        rep = git_coverage(str(args.receipts), match=args.match, repo=args.repo)
    if getattr(args, "format", "text") == "json":
        print(json.dumps(rep, indent=2))
        return
    if rep.get("status_counts"):
        print("Release coverage · PyPI instrument")
    else:
        print(coverage_headline([rep]))
    anchor_unit = "published releases" if rep["anchor"] == "pypi" else "stable package tags"
    print(
        f"  anchor={rep['anchor']}  "
        f"{rep['receipted']}/{rep['total_anchored']} {anchor_unit} with verified receipts"
    )
    counts = rep.get("status_counts")
    if counts:
        print(
            "  "
            + " · ".join(
                f"{name}={counts[name]}"
                for name in ("contemporaneous", "reconstructed", "missing", "invalid")
            )
        )
        for row in rep["releases"]:
            receipt = f"  receipt={row['receipt']}" if row.get("receipt") else ""
            print(f"    {row['version']:<12} {row['status']}{receipt}")
        if rep.get("candidates"):
            print(f"  candidates ({len(rep['candidates'])}) — excluded from the release denominator:")
            for candidate in rep["candidates"]:
                print(f"    · {candidate['version']}  {candidate['path']}")
        if rep.get("invalid_receipts"):
            print(f"  invalid artifacts ({len(rep['invalid_receipts'])}):")
            for invalid in rep["invalid_receipts"]:
                print(f"    · {invalid.get('path', '?')}: {invalid['reason']}")
    if rep["unreceipted_delta"]:
        print(f"  unreceipted delta ({len(rep['unreceipted_delta'])}) — anchored, no receipt:")
        for a in rep["unreceipted_delta"]:
            print(f"    · {a}")
    else:
        print("  no unreceipted delta against this anchor")


def _cmd_receipt_check_equivocation(args: argparse.Namespace) -> None:
    """Check the experimental, objective same-size equivocation predicate."""
    from bulla.experimental.equivocation import verify_equivocation_evidence

    try:
        document = json.loads(args.evidence.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Error: could not read equivocation evidence: {exc}", file=sys.stderr)
        sys.exit(2)
    result = verify_equivocation_evidence(document)
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        if result["equivocation"]:
            print(
                "EQUIVOCATION  "
                f"operator={result['operator_id']} log={result['log_id']} "
                f"tree_size={result['tree_size']}"
            )
            for root in result["roots"]:
                print(f"  root {root}")
        else:
            print("NOT ESTABLISHED")
            for reason in result["reasons"]:
                print(f"  · {reason}")
    sys.exit(0 if result["equivocation"] else 1)


def _cmd_experimental(args: argparse.Namespace) -> None:
    print(
        "usage: bulla experimental "
        "<invent|verify-invention|explain-invention|select-invention|"
        "apply-invention|plan-enrichment|respond-enrichment|refine-envelope|"
        "verify-refinement|assess-finality|repair-reliance|checkpoint> ..."
    )
    print("Research-only surface; no output is part of the stable Bulla API.")
    sys.exit(2)


def _load_invention_problem(path: Path):
    from bulla.experimental.invention import SeamProblem

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        return SeamProblem.from_dict(document)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Error: invalid seam problem: {exc}", file=sys.stderr)
        sys.exit(2)


def _load_invention_result(path: Path):
    from bulla.experimental.invention import SynthesisResult

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        return SynthesisResult.from_dict(document)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Error: invalid synthesis result: {exc}", file=sys.stderr)
        sys.exit(2)


def _cmd_experimental_invent(args: argparse.Namespace) -> None:
    from datetime import datetime, timezone

    from bulla.experimental.invention import mint_invention_receipt, synthesize

    problem = _load_invention_problem(args.problem)
    result = synthesize(problem)
    rendered = json.dumps(result.to_dict(), indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)

    if args.receipt:
        missing = [
            name
            for name in ("principal", "policy", "forum_endpoint", "forum_root")
            if not getattr(args, name)
        ]
        if missing:
            print(
                "Error: --receipt requires " + ", ".join("--" + x.replace("_", "-") for x in missing),
                file=sys.stderr,
            )
            sys.exit(2)
        from bulla.envelope import (
            Authority,
            Bounds,
            Forum,
            Recourse,
            RecourseEnvelope,
            Remedy,
        )

        envelope = RecourseEnvelope(
            authority=Authority(principal=args.principal, policy=args.policy),
            bounds=Bounds(scope=args.receipt_scope or f"predicate invention for {problem.problem_id}"),
            recourse=Recourse(
                challenge_window=args.challenge_window,
                forum=Forum(
                    log_endpoint=args.forum_endpoint,
                    trusted_root_ref=args.forum_root,
                ),
                remedies=(
                    Remedy(
                        rung="recompute",
                        verifier="bulla experimental verify-invention",
                        anchor=result.result_hash,
                    ),
                    Remedy(
                        rung="escalate",
                        verifier=args.principal,
                        anchor=args.policy,
                    ),
                ),
            ),
            retention_class="authority-permanent",
            disclosure_class="auditor",
        )
        receipt = mint_invention_receipt(
            problem,
            result,
            envelope=envelope,
            timestamp=datetime.now(timezone.utc).isoformat(),
            producer={"bulla_version": __version__, "surface": "experimental"},
        )
        args.receipt.write_text(receipt.to_json() + "\n", encoding="utf-8")

    if args.output:
        print(
            f"{result.status.value}  result={args.output}  hash={result.result_hash}",
            file=sys.stderr,
        )
    if args.receipt:
        print(f"receipt={args.receipt}", file=sys.stderr)


def _cmd_experimental_verify_invention(args: argparse.Namespace) -> None:
    from bulla.experimental.invention import (
        GateStatus,
        SynthesisStatus,
        verify_failure_certificate,
        verify_package,
    )

    problem = _load_invention_problem(args.problem)
    result = _load_invention_result(args.result)
    binding_ok = result.problem_hash == problem.problem_hash
    package_report = (
        verify_package(problem, result.package)
        if result.package is not None
        else None
    )
    certificate_valid = (
        verify_failure_certificate(
            problem,
            result.certificate,
            alternatives=result.alternatives,
        )
        if result.certificate is not None
        else None
    )
    minimality_ok = bool(
        package_report is not None
        and result.package is not None
        and (
            (
                result.package.cost.get("minimality")
                == "exact-finite-candidate-space"
                and package_report.minimality is GateStatus.PASS
            )
            or (
                result.package.cost.get("minimality") == "unresolved"
                and package_report.minimality is GateStatus.UNRESOLVED
            )
        )
    )
    if result.status is SynthesisStatus.COMPILED:
        ok = bool(
            binding_ok
            and package_report is not None
            and package_report.gluing is GateStatus.PASS
            and package_report.conservativity is GateStatus.PASS
            and package_report.definability is GateStatus.PASS
            and package_report.preserved_refusals is GateStatus.PASS
            and package_report.receipt_binding is GateStatus.PASS
            and minimality_ok
        )
    elif result.status is SynthesisStatus.PARTIAL:
        ok = bool(
            binding_ok
            and package_report is not None
            and package_report.gluing is GateStatus.PASS
            and package_report.conservativity is GateStatus.PASS
            and package_report.preserved_refusals is GateStatus.PASS
            and package_report.receipt_binding is GateStatus.PASS
            and minimality_ok
            and certificate_valid
        )
    elif result.status in (SynthesisStatus.ESCALATE, SynthesisStatus.CHOICE_REQUIRED):
        ok = bool(binding_ok and certificate_valid)
    else:
        ok = False
    payload = {
        "ok": ok,
        "status": result.status.value,
        "problem_binding": binding_ok,
        "package_gates": package_report.to_dict() if package_report is not None else None,
        "certificate_valid": certificate_valid,
        "result_hash": result.result_hash,
    }
    if args.format == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(f"{'PASS' if ok else 'FAIL'}  {result.status.value}")
        print(f"  problem_binding={binding_ok}")
        if package_report is not None:
            for name, value in package_report.to_dict().items():
                if name != "reasons":
                    print(f"  {name}={value}")
            for reason in package_report.reasons:
                print(f"  reason={reason}")
        if certificate_valid is not None:
            print(f"  certificate_valid={certificate_valid}")
    sys.exit(0 if ok else 1)


def _cmd_experimental_explain_invention(args: argparse.Namespace) -> None:
    result = _load_invention_result(args.result)
    print(f"Outcome: {result.status.value}")
    print(f"Problem: {result.problem_hash}")
    if result.package is not None:
        print(f"Package: {result.package.package_hash} ({result.package.mode})")
        print(
            "Gates: "
            + ", ".join(
                f"{name}={value}"
                for name, value in result.gate_report.to_dict().items()
                if name != "reasons"
            )
        )
        if result.package.mode == "partial":
            print("Residual: ESCALATE (the shared vocabulary does not determine it)")
    if result.certificate is not None:
        print(f"Certificate: {result.certificate.kind.value}")
        print(f"  {result.certificate.statement}")
    if result.alternatives:
        print(f"Choices: {len(result.alternatives)} exact-minimal packages")
        for package in result.alternatives:
            print(f"  {package.package_hash}")
    for reason in result.gate_report.reasons:
        print(f"Reason: {reason}")


def _cmd_experimental_apply_invention(args: argparse.Namespace) -> None:
    from bulla.experimental.control_plane import apply_package

    problem = _load_invention_problem(args.problem)
    result = _load_invention_result(args.result)
    package = result.package
    if args.package_hash:
        package = next(
            (item for item in result.alternatives if item.package_hash == args.package_hash),
            None,
        )
    if package is None:
        print("Error: result has no selected executable package", file=sys.stderr)
        sys.exit(2)
    try:
        structure = json.loads(args.structure.read_text(encoding="utf-8"))
        application = apply_package(
            problem,
            package,
            shared_structure=structure,
            target_arguments=args.argument,
            adapter_version=args.adapter_version,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Error: cannot apply invention: {exc}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(application.to_dict(), indent=2))
    sys.exit(0 if application.status.value == "RELY" else 1)


def _cmd_experimental_select_invention(args: argparse.Namespace) -> None:
    from datetime import datetime, timezone

    from bulla.action_receipt import sign_action_receipt
    from bulla.envelope import Authority, Bounds, RecourseEnvelope
    from bulla.experimental.control_plane import mint_selection_receipt

    problem = _load_invention_problem(args.problem)
    result = _load_invention_result(args.result)
    signer = _load_signer_or_exit(args.key, args.issuer)
    envelope = RecourseEnvelope(
        authority=Authority(principal=args.principal, policy=args.policy),
        bounds=Bounds(scope=args.scope),
    )
    try:
        receipt = mint_selection_receipt(
            problem,
            result,
            selected_package_hash=args.package_hash,
            envelope=envelope,
            timestamp=args.timestamp or datetime.now(timezone.utc).isoformat(),
            producer={"bulla_version": __version__, "surface": "experimental"},
        )
        signed = sign_action_receipt(receipt, signer)
        args.output.write_text(signed.to_json() + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"Error: cannot select invention: {exc}", file=sys.stderr)
        sys.exit(2)
    print(f"selection={args.output} package={args.package_hash}")


def _cmd_experimental_repair_reliance(args: argparse.Namespace) -> None:
    from bulla.experimental.repairs import RepairCatalog, minimal_repairs
    from bulla.reliance import ReliancePolicy

    try:
        view = json.loads(args.verification.read_text(encoding="utf-8"))
        policy = ReliancePolicy(**json.loads(args.policy.read_text(encoding="utf-8")))
        catalog = RepairCatalog.from_dict(json.loads(args.catalog.read_text(encoding="utf-8")))
        plans = minimal_repairs(view, policy, catalog)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"Error: cannot compute repair antichain: {exc}", file=sys.stderr)
        sys.exit(2)
    payload = {
        "catalog_hash": catalog.catalog_hash,
        "exact_within_declared_catalog": True,
        "plans": [plan.to_dict() for plan in plans],
    }
    rendered = json.dumps(payload, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    sys.exit(0 if plans else 1)


def _load_json_or_exit(path: Path, *, label: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Error: invalid {label}: {exc}", file=sys.stderr)
        sys.exit(2)


def _write_json_or_print(document, output: Path | None) -> None:
    rendered = json.dumps(document, indent=2) + "\n"
    if output is None:
        print(rendered, end="")
    else:
        output.write_text(rendered, encoding="utf-8")


def _cmd_experimental_plan_enrichment(args: argparse.Namespace) -> None:
    from bulla.experimental.observability import (
        ConservationManifest,
        LogicPassport,
        ObservableOffer,
        build_enrichment_request,
        plan_enrichment,
    )

    problem = _load_invention_problem(args.problem)
    result = _load_invention_result(args.result)
    catalog = _load_json_or_exit(args.offers, label="observable catalog")
    if isinstance(catalog, dict) and set(catalog) == {"offers"}:
        catalog = catalog["offers"]
    if not isinstance(catalog, list):
        print("Error: observable catalog must be an array or {'offers': [...]}", file=sys.stderr)
        sys.exit(2)
    try:
        offers = tuple(ObservableOffer.from_dict(item) for item in catalog)
        passport = (
            LogicPassport.from_dict(_load_json_or_exit(args.passport, label="logic passport"))
            if args.passport
            else LogicPassport.for_problem(problem)
        )
        manifest = (
            ConservationManifest.from_dict(
                _load_json_or_exit(args.manifest, label="conservation manifest")
            )
            if args.manifest
            else ConservationManifest.for_problem(problem)
        )
        planning = plan_enrichment(
            problem,
            offers,
            passport=passport,
            manifest=manifest,
        )
        request = build_enrichment_request(
            problem,
            result,
            planning,
            offers,
            passport=passport,
            manifest=manifest,
            requester_authority=problem.authority,
        )
    except (KeyError, TypeError, ValueError) as exc:
        print(f"Error: enrichment planning failed: {exc}", file=sys.stderr)
        sys.exit(2)
    packet = {
        "schema_version": "0.1-experimental",
        "problem_hash": problem.problem_hash,
        "result_hash": result.result_hash,
        "passport": passport.to_dict(),
        "manifest": manifest.to_dict(),
        "offers": [offer.to_dict() for offer in offers],
        "planning": planning.to_dict(),
        "request": request.to_dict(),
    }
    _write_json_or_print(packet, args.output)


def _load_enrichment_packet(path: Path):
    from bulla.experimental.observability import (
        ConservationManifest,
        EnrichmentPlanningResult,
        EnrichmentRequest,
        LogicPassport,
        ObservableOffer,
    )

    document = _load_json_or_exit(path, label="enrichment packet")
    expected = {
        "schema_version",
        "problem_hash",
        "result_hash",
        "passport",
        "manifest",
        "offers",
        "planning",
        "request",
    }
    if not isinstance(document, dict) or set(document) != expected:
        raise ValueError(f"enrichment packet fields must be exactly {sorted(expected)}")
    if document["schema_version"] != "0.1-experimental":
        raise ValueError("unsupported enrichment packet schema")
    return (
        document,
        LogicPassport.from_dict(document["passport"]),
        ConservationManifest.from_dict(document["manifest"]),
        tuple(ObservableOffer.from_dict(item) for item in document["offers"]),
        EnrichmentPlanningResult.from_dict(document["planning"]),
        EnrichmentRequest.from_dict(document["request"]),
    )


def _cmd_experimental_respond_enrichment(args: argparse.Namespace) -> None:
    from bulla.experimental.observability import (
        EnrichmentResponse,
        ObservableOffer,
        ProvidedFact,
        ResponseStatus,
        sign_enrichment_response,
    )

    try:
        _, _, _, _, _, request = _load_enrichment_packet(args.packet)
        signer = _load_signer_or_exit(args.key, args.issuer)
        status = ResponseStatus(args.status)
        facts_doc = (
            _load_json_or_exit(args.facts, label="provided facts") if args.facts else []
        )
        counteroffers_doc = (
            _load_json_or_exit(args.counteroffers, label="counteroffers")
            if args.counteroffers
            else []
        )
        if not isinstance(facts_doc, list) or not isinstance(counteroffers_doc, list):
            raise ValueError("facts and counteroffers must be JSON arrays")
        response = sign_enrichment_response(
            EnrichmentResponse(
                request_hash=request.request_hash,
                responder=signer.issuer,
                status=status,
                selected_plan_hash=args.plan_hash,
                provided_facts=tuple(ProvidedFact.from_dict(item) for item in facts_doc),
                counteroffers=tuple(
                    ObservableOffer.from_dict(item) for item in counteroffers_doc
                ),
                reason=args.reason or "",
            ),
            signer,
        )
    except (KeyError, TypeError, ValueError) as exc:
        print(f"Error: cannot sign enrichment response: {exc}", file=sys.stderr)
        sys.exit(2)
    _write_json_or_print(response.to_dict(), args.output)


def _cmd_experimental_refine_envelope(args: argparse.Namespace) -> None:
    from bulla.experimental.observability import EnrichmentResponse
    from bulla.experimental.refinement import (
        authority_epoch,
        build_evidence_admission,
        refine_envelope,
    )

    problem = _load_invention_problem(args.problem)
    prior_result = _load_invention_result(args.prior_result)
    try:
        _, passport, manifest, _, _, request = _load_enrichment_packet(args.packet)
        responses = tuple(
            EnrichmentResponse.from_dict(
                _load_json_or_exit(path, label=f"enrichment response {path}")
            )
            for path in args.response
        )
        admission = build_evidence_admission(
            problem,
            request,
            selected_plan_hash=args.plan_hash,
            responses=responses,
            passport=passport,
            manifest=manifest,
            epoch=authority_epoch(problem.authority),
        )
        bundle = refine_envelope(
            problem,
            prior_result,
            admission,
            passport=passport,
            manifest=manifest,
        )
    except (KeyError, TypeError, ValueError) as exc:
        print(f"Error: refinement failed: {exc}", file=sys.stderr)
        sys.exit(2)
    _write_json_or_print(bundle.to_dict(), args.output)


def _cmd_experimental_verify_refinement(args: argparse.Namespace) -> None:
    from bulla.experimental.refinement import RefinementBundle, verify_refinement

    try:
        bundle = RefinementBundle.from_dict(
            _load_json_or_exit(args.bundle, label="refinement bundle")
        )
        ok = verify_refinement(bundle)
        payload = {
            "ok": ok,
            "bundle_hash": bundle.bundle_hash,
            "certificate_hash": bundle.certificate.certificate_hash,
            "gates": bundle.certificate.to_dict(),
        }
    except (KeyError, TypeError, ValueError) as exc:
        payload = {"ok": False, "error": str(exc)}
    print(json.dumps(payload, indent=2))
    sys.exit(0 if payload["ok"] else 1)


def _cmd_experimental_assess_finality(args: argparse.Namespace) -> None:
    """Run the closed Semantic Finality v0.1 decision order."""

    from bulla.experimental.constitutional import ModelClosureWarrant
    from bulla.experimental.refinement import EnvelopeSnapshot
    from bulla.experimental.semantic_finality import (
        AmbiguityReserve,
        ConsequenceProfile,
        ExternalLock,
        SemanticFinalityPolicy,
        assess_finality,
    )

    try:
        case = _load_json_or_exit(args.case, label="semantic finality case")
        assessment = assess_finality(
            snapshot=EnvelopeSnapshot.from_dict(case["snapshot"]),
            current_semantic_epoch=case["current_semantic_epoch"],
            closure_warrant=ModelClosureWarrant.from_dict(case["closure_warrant"]),
            authority_regime_hash=case["authority_regime_hash"],
            consequence_profile=ConsequenceProfile.from_dict(case["consequence_profile"]),
            represented_outcomes=tuple(case["represented_outcomes"]),
            policy=SemanticFinalityPolicy.from_dict(case["policy"]),
            certified_surface=case["certified_surface"],
            reserve=(AmbiguityReserve.from_dict(case["reserve"]) if case.get("reserve") else None),
            external_lock=(ExternalLock.from_dict(case["external_lock"]) if case.get("external_lock") else None),
            conflict_certificate_hash=case.get("conflict_certificate_hash"),
            evidence_plan_hashes=tuple(case.get("evidence_plan_hashes", ())),
            evidence_classes=tuple(case.get("evidence_classes", ())),
            route_options=tuple(case.get("route_options", ())),
            receipt_references=tuple(case.get("receipt_references", ())),
            action_type=case.get("action_type", "procurement.payment"),
        )
        payload = assessment.to_dict()
        payload["assessment_hash"] = assessment.assessment_hash
    except (KeyError, TypeError, ValueError) as exc:
        print(f"Error: finality assessment failed: {exc}", file=sys.stderr)
        sys.exit(2)
    _write_json_or_print(payload, args.output)


def _cmd_experimental_explain_finality(args: argparse.Namespace) -> None:
    """Emit or independently replay a finite finality-obstruction explanation."""

    from bulla.experimental.claim_flow import (
        DerivationBudgetPolicy,
        FinalityProblem,
        explain_finality,
    )

    try:
        case = _load_json_or_exit(args.case, label="finality explanation case")
        problem = FinalityProblem.from_dict(case["problem"])
        budget = DerivationBudgetPolicy.from_dict(case["budget"])
        explanation = explain_finality(
            problem,
            budget=budget,
            backend_hash=case["backend_hash"],
            backend_version_hash=case["backend_version_hash"],
            run_sequence=case["run_sequence"],
            observed_wall_millis=case.get("observed_wall_millis", 0),
            observed_peak_memory_bytes=case.get("observed_peak_memory_bytes", 0),
        )
        payload = explanation.to_dict()
        payload["explanation_hash"] = explanation.explanation_hash
        if args.verify is not None:
            expected = _load_json_or_exit(args.verify, label="finality explanation")
            expected = dict(expected)
            expected.pop("explanation_hash", None)
            ok = expected == explanation.to_dict()
            payload = {
                "ok": ok,
                "problem_hash": problem.problem_hash,
                "explanation_hash": explanation.explanation_hash,
                "cause": explanation.cause if not ok else "REPLAY_AGREES",
            }
            print(json.dumps(payload, indent=2))
            sys.exit(0 if ok else 1)
    except (KeyError, TypeError, ValueError) as exc:
        print(f"Error: finality explanation failed: {exc}", file=sys.stderr)
        sys.exit(2)
    _write_json_or_print(payload, args.output)


def _cmd_experimental_checkpoint_issue(args: argparse.Namespace) -> None:
    from datetime import datetime, timezone

    from bulla.experimental.checkpoint import CheckpointArchive, WitnessCheckpoint, issue_checkpoint
    from bulla.registry import DeedLog

    signer = _load_signer_or_exit(args.key, args.issuer)
    try:
        previous = (
            WitnessCheckpoint.from_dict(json.loads(args.previous.read_text(encoding="utf-8")))
            if args.previous
            else None
        )
        log = DeedLog(args.registry)
        checkpoint = issue_checkpoint(
            log,
            signer,
            log_id=args.log_id,
            previous=previous,
            issued_at=args.issued_at or datetime.now(timezone.utc).isoformat(),
        )
        consistency = log.consistency(previous.tree_size) if previous is not None else None
        if args.consistency_output:
            if consistency is None:
                raise ValueError("--consistency-output requires --previous")
            args.consistency_output.write_text(
                json.dumps(consistency, indent=2) + "\n", encoding="utf-8"
            )
        if args.archive:
            CheckpointArchive(args.archive).append(
                checkpoint,
                consistency_from_previous=consistency,
            )
        args.output.write_text(json.dumps(checkpoint.to_dict(), indent=2) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Error: cannot issue checkpoint: {exc}", file=sys.stderr)
        sys.exit(2)
    print(f"checkpoint={args.output} size={checkpoint.tree_size} root={checkpoint.root}")


def _cmd_experimental_checkpoint_serve(args: argparse.Namespace) -> None:
    from bulla.experimental.checkpoint import CheckpointArchive, make_checkpoint_server

    try:
        archive = CheckpointArchive(args.archive)
        server = make_checkpoint_server(archive, args.host, args.port)
    except (OSError, ValueError) as exc:
        print(f"Error: cannot serve checkpoint archive: {exc}", file=sys.stderr)
        sys.exit(2)
    host, port = server.server_address[:2]
    print(f"checkpoint archive serving read-only at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _cmd_experimental_checkpoint_verify(args: argparse.Namespace) -> None:
    from bulla.experimental.checkpoint import (
        WitnessCheckpoint,
        verify_checkpoint,
        verify_checkpoint_extension,
    )

    try:
        checkpoint = WitnessCheckpoint.from_dict(
            json.loads(args.checkpoint.read_text(encoding="utf-8"))
        )
        report = verify_checkpoint(checkpoint)
        extension = None
        if args.previous or args.consistency:
            if not args.previous or not args.consistency:
                raise ValueError("--previous and --consistency must be supplied together")
            previous = WitnessCheckpoint.from_dict(
                json.loads(args.previous.read_text(encoding="utf-8"))
            )
            consistency = json.loads(args.consistency.read_text(encoding="utf-8"))
            extension = verify_checkpoint_extension(previous, checkpoint, consistency)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Error: invalid checkpoint: {exc}", file=sys.stderr)
        sys.exit(2)
    payload = {
        "ok": report.ok and (extension is None or extension.ok),
        "checkpoint": report.to_dict(),
        "extension": extension.to_dict() if extension is not None else None,
    }
    print(json.dumps(payload, indent=2))
    sys.exit(0 if payload["ok"] else 1)


def _cmd_experimental_check_candidate(args: argparse.Namespace) -> None:
    from bulla.experimental.hybrid import (
        CandidateProvenance,
        DisclosureBudget,
        HybridStatus,
        check_candidate,
    )

    problem = _load_invention_problem(args.problem)
    try:
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
        budget_doc = json.loads(args.budget.read_text(encoding="utf-8"))
        budget = DisclosureBudget(
            allowed_relations=tuple(budget_doc["allowed_relations"]),
            reveal_target_value=budget_doc["reveal_target_value"],
            max_countermodels=budget_doc["max_countermodels"],
            max_ground_facts=budget_doc["max_ground_facts"],
        )
        provenance = CandidateProvenance(
            args.generator,
            args.generator_version,
            args.prompt_hash,
            args.attempt,
        )
        result = check_candidate(
            problem,
            candidate,
            provenance=provenance,
            disclosure_budget=budget,
            emitted_countermodels=args.emitted_countermodels,
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(f"Error: cannot check candidate: {exc}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(result.to_dict(), indent=2))
    sys.exit(0 if result.status is HybridStatus.ACCEPTED else 1)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="bulla",
        description=(
            "Recomputable receipts for authorless agent action. "
            "Trunk: `receipt create` / `receipt verify` (one consequential action, "
            "made accountable — authority, bounds, recourse, grounding, coined "
            "conventions) and `coverage` (which anchored actions left NO receipt). "
            "Diagnostic layer: `compose` / `audit` / `gauge` measure seam blind "
            "spots and the coherence fee — a disclosure signal a receipt can carry."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"bulla {__version__}"
    )

    subparsers = parser.add_subparsers(dest="command")

    # ── diagnose ──────────────────────────────────────────────────────
    p_diag = subparsers.add_parser(
        "diagnose",
        help="Diagnose compositions and report blind spots",
    )
    p_diag.add_argument(
        "files", nargs="*", type=Path,
        help="YAML composition file(s) or directories",
    )
    p_diag.add_argument(
        "--format",
        choices=["text", "json", "sarif"],
        default="text",
        help="Output format (default: text)",
    )
    p_diag.add_argument(
        "--brief",
        action="store_true",
        help="One-line-per-file summary (fee + blind spot count only)",
    )
    p_diag.add_argument(
        "--examples",
        action="store_true",
        help="Run on bundled example compositions",
    )
    p_diag.add_argument(
        "--witness",
        action="store_true",
        help=(
            "Include witness-geometry diagnostics (leverage scores, "
            "N_eff concentration, coloops/loops, greedy minimum-cost "
            "disclosure basis). Computed only when fee > 0."
        ),
    )
    p_diag.add_argument(
        "--regime",
        action="store_true",
        help=(
            "Include the regime block in JSON output (Sprint 11 regime "
            "lattice classification). Default off — preserves byte-"
            "identity with the 0.34.0 golden JSON fixture. Use "
            "`bulla regime <path>` for a standalone regime classification."
        ),
    )
    _add_pack_args(p_diag)
    p_diag.set_defaults(func=_cmd_diagnose)

    # ── regime ────────────────────────────────────────────────────────
    p_regime = subparsers.add_parser(
        "regime",
        help="Print the regime classification of one or more compositions",
    )
    p_regime.add_argument(
        "files", nargs="+", type=Path,
        help="YAML composition file(s) or directories",
    )
    p_regime.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    _add_pack_args(p_regime)
    p_regime.set_defaults(func=_cmd_regime)

    # ── certify ───────────────────────────────────────────────────────
    # Sprint 13: per-composition certificate orchestrator.
    p_certify = subparsers.add_parser(
        "certify",
        help=(
            "Emit per-composition certificate(s): regime + fee + "
            "interpretation + repair semantics in one bundled JSON artifact"
        ),
    )
    p_certify.add_argument(
        "files", nargs="*", type=Path,
        help="YAML composition file(s) or directories",
    )
    p_certify.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    p_certify.add_argument(
        "--seed-set",
        action="store_true",
        help=(
            "Emit certificates for the canonical Sprint 13 seed set "
            "(10 compositions covering the regime lattice)"
        ),
    )
    p_certify.add_argument(
        "--output",
        type=str,
        default=None,
        help="Write certificate(s) to FILE instead of stdout",
    )
    p_certify.add_argument(
        "--sign",
        action="store_true",
        help=(
            "Sign each certificate under an agent identity (requires bulla[identity]); "
            "uses --key or the default key from `bulla key gen`"
        ),
    )
    p_certify.add_argument(
        "--key", type=Path, default=None, metavar="FILE",
        help="ed25519 key file to sign with (default: ~/.bulla/identity.json)",
    )
    p_certify.add_argument(
        "--issuer", type=str, default=None, metavar="URI",
        help=(
            "External issuer URI (did:web:…, eip155:…, an Entra/SPIFFE id) to bind to; "
            "default is the key's self-certifying did:key"
        ),
    )
    _add_pack_args(p_certify)
    p_certify.set_defaults(func=_cmd_certify)

    # ── key ───────────────────────────────────────────────────────────
    # The local ed25519 signing identity. Bulla signs receipts/certificates
    # under an identity the agent already holds; it never issues one.
    p_key = subparsers.add_parser(
        "key", help="Manage the local ed25519 signing identity (did:key)"
    )
    key_sub = p_key.add_subparsers(dest="key_command")
    p_key_gen = key_sub.add_parser(
        "gen", help="Generate a local ed25519 keypair (a self-certifying did:key)"
    )
    p_key_gen.add_argument(
        "-o", "--output", type=Path, default=None, metavar="FILE",
        help="Write the key file (default: ~/.bulla/identity.json)",
    )
    p_key_gen.add_argument(
        "--force", action="store_true", help="Overwrite an existing key file",
    )
    p_key_gen.set_defaults(func=_cmd_key_gen)
    p_key.set_defaults(func=_cmd_key)

    # ── verify ────────────────────────────────────────────────────────
    p_verify = subparsers.add_parser(
        "verify",
        help="Verify a signed certificate: content integrity, signature authenticity, and anchor",
    )
    p_verify.add_argument("certificate", type=Path, help="Certificate JSON file")
    p_verify.add_argument(
        "--key", type=Path, default=None, metavar="FILE",
        help="Public key file to verify a non-did:key issuer against",
    )
    p_verify.add_argument(
        "--registry", type=str, default=None, metavar="PATH_OR_URL",
        help=(
            "Also demand the deed be logged in this registry (a local JSONL path "
            "or an http(s) URL to `bulla registry serve`). Refuses the unlogged "
            "— the omission-closer. Exit code is nonzero if absent."
        ),
    )
    p_verify.add_argument(
        "--trusted-root", type=str, default=None, metavar="HASH",
        help=(
            "Pin the registry root: for a REMOTE registry, the served root must "
            "equal this (obtained out of band), else verify refuses (a host-asserted "
            "root proves nothing). A mismatch is flagged as possible equivocation."
        ),
    )
    p_verify.add_argument(
        "--root-ots", type=str, default=None, metavar="FILE_OR_PROOF",
        help=(
            "An OTS proof (path or base64) anchoring the served root to the "
            "timechain — an alternative to --trusted-root for trusting a remote root."
        ),
    )
    p_verify.add_argument("--format", choices=["text", "json"], default="text")
    p_verify.set_defaults(func=_cmd_verify)

    # ── receipt ───────────────────────────────────────────────────────
    p_receipt = subparsers.add_parser(
        "receipt",
        help=(
            "Create and verify action receipts — the accountable record of one "
            "consequential agent action and the durable object recourse can reference"
        ),
    )
    receipt_sub = p_receipt.add_subparsers(dest="receipt_command")
    p_receipt_create = receipt_sub.add_parser(
        "create",
        help=(
            "Mint an ActionReceipt for one consequential action: act + verdict slot + "
            "mandate/remedy envelope (modality law enforced) + evidence grounding + "
            "coined conventions. Sign with --key; verify with `bulla receipt verify`."
        ),
    )
    p_receipt_create.add_argument(
        "--type", required=True, metavar="ACT",
        help="The act, open vocabulary (e.g. github.create_file, package.release).",
    )
    p_receipt_create.add_argument(
        "--subject", action="append", metavar="K=V",
        help="Subject field (repeatable). Values parse as JSON scalars when they can.",
    )
    p_receipt_create.add_argument(
        "--subject-json", type=Path, default=None, metavar="FILE",
        help="Subject as a JSON object file (merged before --subject overrides).",
    )
    p_receipt_create.add_argument(
        "--diagnostic-ref", default=None, metavar="SHA",
        help="The recomputable verdict this act ran under (sets status=reference).",
    )
    p_receipt_create.add_argument(
        "--diagnostic-status", choices=["not_applicable", "deferred"], default="not_applicable",
        help="Why there is no verdict reference — never bare null (default: not_applicable).",
    )
    p_receipt_create.add_argument(
        "--evidence", action="append", metavar="NAME=HASH[:GROUNDING]",
        help=(
            "Evidence ref (repeatable). Grounding ∈ {self_asserted, counterparty_signed, "
            "third_party_anchored, execution_verified}; default self_asserted — the "
            "receipt inherits the grounding of its weakest necessary anchor."
        ),
    )
    p_receipt_create.add_argument(
        "--convention", action="append", metavar="FILE",
        help=(
            "A convention entry as JSON (repeatable) — a rule coined at this seam, "
            "committed inside the content hash. definition_hash is computed if absent."
        ),
    )
    p_receipt_create.add_argument("--principal", default=None, help="authority.principal (the surviving principal).")
    p_receipt_create.add_argument("--policy", default=None, help="authority.policy (policy@hash reference).")
    p_receipt_create.add_argument("--scope", default=None, help="bounds.scope for the act.")
    p_receipt_create.add_argument("--challenge-window", default="P7D", metavar="ISO8601-DURATION")
    p_receipt_create.add_argument(
        "--forum-endpoint", required=True, metavar="URL",
        help="Where a challenge is heard (remedy forum).",
    )
    p_receipt_create.add_argument(
        "--forum-root", required=True, metavar="REF",
        help="The root reference YOU pin — never the host's served root (Pin-the-Root).",
    )
    p_receipt_create.add_argument(
        "--remedy", action="append", metavar="RUNG:VERIFIER:ANCHOR",
        help=(
            "A remedy (repeatable): rung ∈ {recompute,challenge,cure,revert,slash,escalate}; "
            "verifier and anchor are required (modality law). "
            "Default: 'recompute:bulla receipt verify:hashes.content'."
        ),
    )
    p_receipt_create.add_argument("--retention", choices=["authority-permanent", "operational", "personal-expiring"], default="operational")
    p_receipt_create.add_argument("--disclosure", choices=["public", "party", "auditor"], default=None)
    p_receipt_create.add_argument("--anchor", default=None, metavar="KIND=REF", help="anchor_ref, e.g. git=commit:abc123.")
    p_receipt_create.add_argument("--key", type=Path, default=None, metavar="FILE", help="ed25519 key (bulla key gen) — signs content_hash.")
    p_receipt_create.add_argument("--issuer", default=None, metavar="URI", help="External issuer URI (default: the key's did:key).")
    p_receipt_create.add_argument("--timestamp", default=None, help="ISO-8601 (default: now, UTC).")
    p_receipt_create.add_argument("--out", type=Path, default=None, metavar="FILE", help="Write here (default: stdout).")
    p_receipt_create.set_defaults(func=_cmd_receipt_create)
    p_receipt_verify = receipt_sub.add_parser(
        "verify",
        help=(
            "Verify a receipt (action / witness / certificate): recompute the four "
            "hashes, re-validate the recourse envelope (modality law), check the "
            "signature — and report how far it got (verified_to: digest|attestation|"
            "log_inclusion) rather than a lying pass/fail boolean"
        ),
    )
    p_receipt_verify.add_argument("receipt", type=Path, help="Receipt JSON file")
    p_receipt_verify.add_argument(
        "--key", type=Path, default=None, metavar="FILE",
        help="Public key file to verify a non-did:key signature against",
    )
    p_receipt_verify.add_argument("--format", choices=["text", "json"], default="text")
    p_receipt_verify.set_defaults(func=_cmd_receipt_verify)
    p_receipt_equivocation = receipt_sub.add_parser(
        "check-equivocation",
        help=(
            "EXPERIMENTAL: authenticate two log heads and establish only the "
            "same-operator, same-log, same-size, different-root predicate"
        ),
    )
    p_receipt_equivocation.add_argument(
        "evidence", type=Path, help="EquivocationEvidence JSON file"
    )
    p_receipt_equivocation.add_argument(
        "--format", choices=["text", "json"], default="text"
    )
    p_receipt_equivocation.set_defaults(func=_cmd_receipt_check_equivocation)
    p_receipt.set_defaults(func=_cmd_receipt)

    # ── coverage ──────────────────────────────────────────────────────
    p_coverage = subparsers.add_parser(
        "coverage",
        help=(
            "Receipt coverage against a declared anchor (omission detection): "
            "which anchored actions have no receipt. Reports coverage relative to "
            "the anchor — never a bare, gameable percentage"
        ),
    )
    p_coverage.add_argument(
        "--anchor", choices=["pypi", "git"], default="pypi",
        help="External release record (default: pypi; git is secondary)",
    )
    p_coverage.add_argument(
        "--receipts", type=Path, required=True, metavar="DIR",
        help="Directory of release ActionReceipts",
    )
    p_coverage.add_argument(
        "--match", default="v[0-9]*", metavar="GLOB",
        help="git tag glob; results are restricted to stable vX.Y.Z package tags",
    )
    p_coverage.add_argument("--repo", default=".", help="Repo path (default: cwd)")
    p_coverage.add_argument("--project", default="bulla", help="PyPI project (default: bulla)")
    p_coverage.add_argument(
        "--snapshot", type=Path, default=None, metavar="FILE",
        help="Read a saved PyPI project JSON response instead of the live API",
    )
    p_coverage.add_argument(
        "--no-integrity", action="store_true",
        help="Do not resolve Integrity API objects (for an offline snapshot check)",
    )
    p_coverage.add_argument(
        "--expected-repository", default="jkomkov/bulla", metavar="OWNER/REPO",
        help="Required GitHub Trusted Publisher identity for contemporaneous receipts",
    )
    p_coverage.add_argument("--format", choices=["text", "json"], default="text")
    p_coverage.set_defaults(func=_cmd_coverage)

    # ── gate ──────────────────────────────────────────────────────────
    p_gate = subparsers.add_parser(
        "gate",
        help=(
            "Recourse gate: PROCEED or REFUSE on a counterparty's deed — inclusion "
            "under a root you trust independently, authenticity, integrity. Exit 0 = "
            "proceed, 1 = refuse (with a contestable refusal certificate). Where `verify` "
            "reports the checks, `gate` enforces the decision. The coherence fee is "
            "reported, never blocked on, unless you opt in with --require-fee."
        ),
    )
    p_gate.add_argument(
        "--certificate", type=Path, default=None, metavar="FILE",
        help="The counterparty's full signed certificate (carries the fee; required for --require-fee).",
    )
    p_gate.add_argument(
        "--deed", type=Path, default=None, metavar="FILE",
        help="The counterparty's deed record (the triple) — a fee-blind alternative to --certificate.",
    )
    p_gate.add_argument(
        "--registry", type=str, required=True, metavar="PATH_OR_URL",
        help="The registry you demand inclusion in: a local path or an http(s) URL.",
    )
    p_gate.add_argument(
        "--trusted-root", type=str, default=None, metavar="HASH",
        help="A root you pin INDEPENDENTLY of the host (else a remote, host-asserted root is refused).",
    )
    p_gate.add_argument(
        "--root-ots", type=str, default=None, metavar="FILE_OR_PROOF",
        help="An OTS proof anchoring the served root — an alternative to --trusted-root.",
    )
    p_gate.add_argument(
        "--composition-hash", type=str, default=None, metavar="HASH",
        help="Demand the deed be for THIS composition (fail closed otherwise).",
    )
    p_gate.add_argument(
        "--require-fee", type=int, default=None, metavar="N",
        help=(
            "OPT IN to fee-gating: refuse when the certified coherence_fee exceeds N. "
            "Default: report the fee, do not gate on it (a disclosure signal, not an "
            "execution predictor — see FALSIFICATIONS.md)."
        ),
    )
    p_gate.add_argument(
        "--key", type=Path, default=None, metavar="FILE",
        help="Your ed25519 key (run `bulla key gen`) — signs the refusal certificate so it is non-repudiable.",
    )
    p_gate.add_argument(
        "--issuer", type=str, default=None, metavar="URI",
        help="External issuer URI for your signing key (default: its did:key).",
    )
    p_gate.add_argument(
        "--disclose", action="append", default=None, metavar="DIM",
        help="Name a convention the cure must disclose (repeatable; e.g. --disclose path_root).",
    )
    p_gate.add_argument("--format", choices=["text", "brief", "json"], default="text")
    p_gate.set_defaults(func=_cmd_gate)

    # ── anchor ────────────────────────────────────────────────────────
    p_anchor = subparsers.add_parser(
        "anchor",
        help="Anchor a signed certificate to the Bitcoin timechain (writes a .ots sidecar)",
    )
    p_anchor.add_argument("certificate", type=Path, help="Signed certificate JSON file")
    p_anchor.set_defaults(func=_cmd_anchor)

    # ── registry ──────────────────────────────────────────────────────
    # The append-only deed log: the audit layer under signed deeds. This is the
    # auditable reference primitive; the operated, distributed registry is the
    # product. It closes deletion/reordering and makes omission *checkable*; it
    # does not close omission itself (a relying party must demand inclusion) and
    # does not resist rekey (that's the external identity's job).
    p_registry = subparsers.add_parser(
        "registry",
        help="Append-only deed log: append, enumerate, and prove signed certificates",
    )
    reg_sub = p_registry.add_subparsers(dest="registry_command")

    def _reg_log_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--log", type=Path, default=None, metavar="FILE",
            help="Deed log file (default: ~/.bulla/registry.jsonl)",
        )

    p_reg_append = reg_sub.add_parser("append", help="Append a signed certificate as a deed")
    p_reg_append.add_argument("certificate", type=Path, help="Signed certificate JSON")
    _reg_log_arg(p_reg_append)
    p_reg_append.set_defaults(func=_cmd_registry_append)

    p_reg_log = reg_sub.add_parser(
        "log", help="Enumerate the logged deeds (the audit query; optionally one issuer)"
    )
    p_reg_log.add_argument("--issuer", type=str, default=None, metavar="URI")
    p_reg_log.add_argument("--format", choices=["text", "json"], default="text")
    _reg_log_arg(p_reg_log)
    p_reg_log.set_defaults(func=_cmd_registry_log)

    p_reg_prove = reg_sub.add_parser("prove", help="Emit an inclusion proof for a deed index")
    p_reg_prove.add_argument("index", type=int, help="Leaf index")
    _reg_log_arg(p_reg_prove)
    p_reg_prove.set_defaults(func=_cmd_registry_prove)

    p_reg_root = reg_sub.add_parser(
        "root", help="Print the current Merkle root (anchor it to timestamp the whole log)"
    )
    _reg_log_arg(p_reg_root)
    p_reg_root.set_defaults(func=_cmd_registry_root)

    p_reg_anchor = reg_sub.add_parser(
        "anchor", help="Anchor the current root to the Bitcoin timechain (a log checkpoint)"
    )
    _reg_log_arg(p_reg_anchor)
    p_reg_anchor.set_defaults(func=_cmd_registry_anchor)

    p_reg_serve = reg_sub.add_parser(
        "serve", help="Serve the registry read-only over HTTP (the online surface)"
    )
    p_reg_serve.add_argument("--host", type=str, default="127.0.0.1", metavar="HOST")
    p_reg_serve.add_argument("--port", type=int, default=8087, metavar="PORT")
    _reg_log_arg(p_reg_serve)
    p_reg_serve.set_defaults(func=_cmd_registry_serve)

    p_registry.set_defaults(func=_cmd_registry)

    # ── certify-update ────────────────────────────────────────────────
    p_certify_update = subparsers.add_parser(
        "certify-update",
        help="Assess semantic compatibility delta between two composition manifests",
    )
    p_certify_update.add_argument(
        "old_file", type=Path,
        help="Old/baseline composition YAML",
    )
    p_certify_update.add_argument(
        "new_file", type=Path,
        help="New/updated composition YAML",
    )
    p_certify_update.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    _add_pack_args(p_certify_update)
    p_certify_update.set_defaults(func=_cmd_certify_update)

    # ── check ─────────────────────────────────────────────────────────
    p_check = subparsers.add_parser(
        "check",
        help="CI/CD gate: exit 1 if compositions exceed thresholds",
    )
    p_check.add_argument(
        "files", nargs="*", type=Path,
        help="YAML composition file(s) or directories",
    )
    p_check.add_argument(
        "--max-blind-spots",
        type=int,
        default=0,
        metavar="N",
        help="Max blind spots per composition before failing (default: 0)",
    )
    p_check.add_argument(
        "--max-unbridged",
        type=int,
        default=0,
        metavar="N",
        help="Max unbridged edges per composition before failing (default: 0)",
    )
    p_check.add_argument(
        "--max-fee",
        type=int,
        default=None,
        metavar="N",
        help="Exit 1 if coherence fee exceeds N",
    )
    p_check.add_argument(
        "--format",
        choices=["text", "json", "sarif"],
        default="text",
        help="Output format (default: text)",
    )
    p_check.add_argument(
        "--examples",
        action="store_true",
        help="Run on bundled example compositions",
    )
    p_check.add_argument(
        "--witness",
        action="store_true",
        help=(
            "Include witness-geometry diagnostics in the text/JSON/SARIF "
            "output (leverage, N_eff, coloops, greedy disclosure basis). "
            "Does not affect check thresholds."
        ),
    )
    p_check.add_argument(
        "--baseline",
        type=Path,
        default=None,
        metavar="RECEIPT.json",
        help=(
            "Compare against a baseline receipt (JSON). "
            "Exit 1 if the baseline is stale (composition or policy "
            "changed) OR if the current state has regressed (higher "
            "fee, worse disposition, new blind spots). Requires "
            "exactly one composition file."
        ),
    )
    p_check.add_argument(
        "--certificate-out",
        type=Path,
        default=None,
        metavar="PATH.json",
        help=(
            "Write a CompositionCertificate (v1.0 schema) for each "
            "checked composition to PATH.json. Single composition: "
            "writes one certificate object. Multiple compositions: "
            "writes a JSON array of certificates. Independent of "
            "the CI gate result; certificates are written before exit "
            "regardless of pass/fail. Unifies the check (CI gate) and "
            "certify (record-keeping) primitives at the v0.38.0 CLI "
            "surface — used by G24 self-host pipeline-CI to record "
            "the certified state at each commit hash in the historical "
            "analysis window."
        ),
    )
    _add_pack_args(p_check)
    p_check.set_defaults(func=_cmd_check)

    # ── diff ──────────────────────────────────────────────────────────
    p_diff = subparsers.add_parser(
        "diff",
        help="Compare two receipts and show what changed",
    )
    p_diff.add_argument(
        "baseline", type=Path,
        help="Baseline receipt (JSON) — the 'before' state",
    )
    p_diff.add_argument(
        "current", type=Path,
        help="Current receipt (JSON) — the 'after' state",
    )
    p_diff.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    p_diff.set_defaults(func=_cmd_diff)

    # ── infer ─────────────────────────────────────────────────────────
    p_infer = subparsers.add_parser(
        "infer",
        help="Infer a proto-composition YAML from an MCP manifest JSON",
    )
    p_infer.add_argument(
        "manifest", type=Path,
        help="Path to an MCP manifest JSON (list_tools response)",
    )
    p_infer.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Write output to file instead of stdout",
    )
    _add_pack_args(p_infer)
    p_infer.set_defaults(func=_cmd_infer)

    # ── scan ──────────────────────────────────────────────────────────
    p_scan = subparsers.add_parser(
        "scan",
        help=(
            "Scan MCP server(s) and diagnose. With no args, "
            "auto-detects the host config; pass commands or --config "
            "for explicit targets."
        ),
    )
    p_scan.add_argument(
        "commands", nargs="*",
        help=(
            "Shell command(s) to start MCP server(s). Omit for "
            "auto-detect of the host's MCP config."
        ),
    )
    p_scan.add_argument(
        "--config", type=Path, default=None,
        help=(
            "Path to an MCP config file (Cursor / Claude Desktop / "
            "Claude Code shape). Skips auto-detect."
        ),
    )
    p_scan.add_argument(
        "--format",
        choices=["narrative", "text", "json", "sarif"],
        default="narrative",
        help=(
            "Output format. 'narrative' (default) is plain prose with "
            "dimension explanations and the pairwise-vs-global "
            "comparison. 'text' is the legacy mathematician-grade "
            "view. 'json' / 'sarif' for programmatic consumers."
        ),
    )
    p_scan.add_argument(
        "--json",
        action="store_true",
        help="Shortcut for --format json (machine-readable receipt).",
    )
    p_scan.add_argument(
        "--no-pairwise",
        action="store_true",
        help=(
            "Skip the pairwise-vs-global comparison block in narrative "
            "output. Useful for very large compositions or when the "
            "n*(n-1)/2 compose_multi calls would slow the scan."
        ),
    )
    p_scan.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Save inferred composition YAML to file",
    )
    _add_pack_args(p_scan)
    p_scan.set_defaults(func=_cmd_scan)

    # ── certify-cost ───────────────────────────────────────────────────
    p_ccost = subparsers.add_parser(
        "certify-cost",
        help="Coherence Cost Certificate (v0): the irreducible coherence floor "
             "+ witness fields; with --observed-cost, the unexplained premium",
    )
    p_ccost.add_argument("composition", type=Path, help="composition JSON file")
    p_ccost.add_argument("--observed-cost", type=float, default=None,
                         help="the intermediary's observed charge, in your unit")
    p_ccost.set_defaults(func=_cmd_certify_cost)

    # ── gauge ──────────────────────────────────────────────────────────
    p_gauge = subparsers.add_parser(
        "gauge",
        help="Diagnose an MCP server or manifest with prescriptive disclosure",
    )
    gauge_input = p_gauge.add_mutually_exclusive_group(required=True)
    gauge_input.add_argument(
        "manifest", nargs="?", type=Path, default=None,
        help="MCP manifest JSON file",
    )
    gauge_input.add_argument(
        "--mcp-server", metavar="CMD",
        help="Shell command to start MCP server",
    )
    p_gauge.add_argument(
        "--format",
        choices=["text", "json", "sarif"],
        default="text",
        help="Output format (default: text)",
    )
    p_gauge.add_argument(
        "-o", "--output-composition", type=Path, metavar="FILE",
        help="Save inferred composition YAML to file",
    )
    p_gauge.add_argument(
        "--max-fee", type=int, default=None, metavar="N",
        help="Exit 1 if coherence fee exceeds N (CI gating)",
    )
    p_gauge.add_argument(
        "--max-blind-spots", type=int, default=None, metavar="N",
        help="Exit 1 if blind spots exceed N (CI gating)",
    )
    p_gauge.add_argument(
        "-v", "--verbose", action="store_true",
        help="Show full blind spot details and bridge recommendations",
    )
    p_gauge.add_argument(
        "--leverage",
        action="store_true",
        help=(
            "Include witness-geometry diagnostics in the output "
            "(per-field leverage, N_eff, coloops, greedy minimum-cost "
            "disclosure basis)."
        ),
    )
    p_gauge.add_argument(
        "--substitutes",
        nargs=2,
        metavar=("TOOL", "FIELD"),
        default=None,
        help=(
            "Show top-3 disclosure substitutes for the given hidden field, "
            "ranked by effective resistance in the Kron-reduced witness "
            "geometry. Takes two positional arguments: tool name and "
            "field name (dot-safe)."
        ),
    )
    p_gauge.add_argument(
        "--costs",
        type=Path,
        default=None,
        metavar="FILE",
        help=(
            "YAML file mapping '<tool>:<field>' -> rational cost string "
            "('p/q' or integer). Runs the matroid-greedy minimum-cost "
            "disclosure algorithm (optimal by Edmonds 1971)."
        ),
    )
    _add_pack_args(p_gauge)
    p_gauge.set_defaults(func=_cmd_gauge)

    # ── audit ──────────────────────────────────────────────────────────
    p_audit = subparsers.add_parser(
        "audit",
        help="Audit all MCP servers in a config file (cross-server diagnosis)",
    )
    p_audit.add_argument(
        "config", nargs="?", type=Path, default=None,
        help="MCP config JSON file (default: auto-detect)",
    )
    p_audit.add_argument(
        "--manifests", type=Path, metavar="DIR",
        help="Directory of pre-captured MCP manifest JSON files (alternative to live scan)",
    )
    p_audit.add_argument(
        "--format",
        choices=["text", "json", "sarif"],
        default="text",
        help="Output format (default: text)",
    )
    p_audit.add_argument(
        "-o", "--output-composition", type=Path, metavar="FILE",
        help="Save combined composition YAML to file",
    )
    p_audit.add_argument(
        "--max-fee", type=int, default=None, metavar="N",
        help="Exit 1 if coherence fee exceeds N (CI gating)",
    )
    p_audit.add_argument(
        "--max-blind-spots", type=int, default=None, metavar="N",
        help="Exit 1 if blind spots exceed N (CI gating)",
    )
    p_audit.add_argument(
        "--max-unmet", type=int, default=None, metavar="N",
        help="Exit 1 if unmet obligations exceed N (CI gating)",
    )
    p_audit.add_argument(
        "--max-contradictions", type=int, default=None, metavar="N",
        help="Exit 1 if convention contradictions exceed N (CI gating)",
    )
    p_audit.add_argument(
        "--max-structural", type=int, default=None, metavar="N",
        help="Exit 1 if structural contradiction score exceeds N (CI gating)",
    )
    p_audit.add_argument(
        "-v", "--verbose", action="store_true",
        help="Show full blind spot details",
    )
    p_audit.add_argument(
        "--skip-failed", action="store_true", default=True,
        help="Continue when individual servers fail (default: true)",
    )
    p_audit.add_argument(
        "--no-skip-failed", action="store_false", dest="skip_failed",
        help="Fail if any server cannot be scanned",
    )
    p_audit.add_argument(
        "--discover", action="store_true", default=False,
        help="Run LLM-powered convention discovery before auditing",
    )
    p_audit.add_argument(
        "--discover-provider",
        choices=["openai", "anthropic", "openrouter", "auto"],
        default="auto",
        metavar="PROVIDER",
        help="LLM provider for --discover (default: auto-detect from env)",
    )
    p_audit.add_argument(
        "--output-discovered", type=Path, metavar="FILE",
        help="Save discovered micro-pack YAML to file (requires --discover)",
    )
    p_audit.add_argument(
        "--guided-discover", action="store_true", default=False,
        help="Run obligation-directed LLM discovery to repair blind spots",
    )
    p_audit.add_argument(
        "--converge", action="store_true", default=False,
        help="Iterative convergence loop (extends --guided-discover)",
    )
    p_audit.add_argument(
        "--max-rounds", type=int, default=5, metavar="N",
        help="Maximum convergence rounds (default: 5, requires --converge)",
    )
    p_audit.add_argument(
        "--receipt", type=Path, metavar="FILE",
        help="Write a WitnessReceipt JSON to file after auditing",
    )
    p_audit.add_argument(
        "--chain", type=Path, metavar="RECEIPT.json",
        help="Load a prior receipt's vocabulary and chain the new receipt",
    )
    p_audit.add_argument(
        "--host", metavar="NAME",
        help="Force a specific MCP host's config (e.g. 'cursor', 'claude-code', 'cline'). "
             "Use 'bulla hosts list' to see registered hosts.",
    )
    _add_pack_args(p_audit)
    p_audit.set_defaults(func=_cmd_audit)

    # ── manifest ──────────────────────────────────────────────────────
    p_manifest = subparsers.add_parser(
        "manifest",
        help="Generate or validate Bulla Manifest files",
    )
    p_manifest.add_argument(
        "--from-json", metavar="FILE",
        help="Generate manifest(s) from an MCP manifest JSON",
    )
    p_manifest.add_argument(
        "--from-server", metavar="CMD",
        help="Generate manifest(s) from a live MCP server command",
    )
    p_manifest.add_argument(
        "--validate", metavar="FILE",
        help="Validate an existing manifest YAML",
    )
    p_manifest.add_argument(
        "--publish", metavar="FILE",
        help="Anchor manifest to Bitcoin timechain via OpenTimestamps (requires bulla[ots])",
    )
    p_manifest.add_argument(
        "--verify", metavar="FILE",
        help="Verify OTS proof on a published manifest",
    )
    p_manifest.add_argument(
        "--upgrade",
        action="store_true",
        help="With --verify: upgrade pending proofs to confirmed",
    )
    p_manifest.add_argument(
        "-o", "--output", metavar="FILE", default=None,
        help="Write output to file instead of stdout",
    )
    p_manifest.add_argument(
        "--examples",
        action="store_true",
        help="Generate example manifests to see the format",
    )
    p_manifest.set_defaults(func=_cmd_manifest)

    # ── bridge ─────────────────────────────────────────────────────────
    p_bridge = subparsers.add_parser(
        "bridge",
        help="Auto-generate bridged composition or JSON patches",
    )
    p_bridge.add_argument(
        "files", nargs="+", type=Path,
        help="YAML composition file(s)",
    )
    p_bridge.add_argument(
        "--format",
        choices=["yaml", "json-patch"],
        default="yaml",
        help="Output format: bridged YAML (default) or JSON patches",
    )
    p_bridge.add_argument(
        "-o", "--output", default=None,
        help="Write output to file instead of stdout",
    )
    p_bridge.set_defaults(func=_cmd_bridge)

    # ── translate (runtime value translation, separate from `bridge`) ──
    p_translate = subparsers.add_parser(
        "translate",
        help="Runtime value translation across conventions on a dimension",
    )
    p_translate.add_argument(
        "--dimension", required=True,
        help="Dimension name (e.g. currency_code, country_code)",
    )
    p_translate.add_argument(
        "--value", required=True,
        help="Value to translate (e.g. USD)",
    )
    p_translate.add_argument(
        "--to", required=True, dest="to",
        help="Target convention id (e.g. stripe-lower, iso-3166-alpha3)",
    )
    p_translate.add_argument(
        "--from", default=None, dest="from_",
        help=(
            "Optional source convention id; if omitted, the runtime "
            "tries every registered translator with matching dimension "
            "and to-convention."
        ),
    )
    p_translate.set_defaults(func=_cmd_translate)

    # ── witness ────────────────────────────────────────────────────────
    p_witness = subparsers.add_parser(
        "witness",
        help="Diagnose and emit a WitnessReceipt (JSON)",
    )
    p_witness.add_argument(
        "files", nargs="+", type=Path,
        help="YAML composition file(s)",
    )
    p_witness.set_defaults(func=_cmd_witness)

    # ── compose (developer-facing prescriptive output) ────────────────
    p_compose = subparsers.add_parser(
        "compose",
        help="Diagnose composition(s) and emit a prescriptive report "
        "(natural-language fix instructions for engineers).",
    )
    p_compose.add_argument(
        "files", nargs="+", type=Path,
        help="YAML composition file(s)",
    )
    p_compose.add_argument(
        "--format",
        choices=["prescriptive", "json"],
        default="prescriptive",
        help="Output format. 'prescriptive' (default) gives human-readable "
        "fix instructions; 'json' emits the same WitnessReceipt as `bulla witness`.",
    )
    p_compose.set_defaults(func=_cmd_compose)

    # ── discover ──────────────────────────────────────────────────────
    p_discover = subparsers.add_parser(
        "discover",
        help="Discover convention dimensions from tool schemas using an LLM",
    )
    p_discover.add_argument(
        "--manifests", type=Path, required=True, metavar="DIR",
        help="Directory of pre-captured MCP manifest JSON files",
    )
    p_discover.add_argument(
        "-o", "--output", type=Path, required=True, metavar="FILE",
        help="Output micro-pack YAML file",
    )
    p_discover.add_argument(
        "--provider",
        choices=["openai", "anthropic", "openrouter", "auto"],
        default="auto",
        help="LLM provider (default: auto-detect from env)",
    )
    _add_pack_args(p_discover)
    p_discover.set_defaults(func=_cmd_discover)

    # ── pack ──────────────────────────────────────────────────────────
    p_pack = subparsers.add_parser(
        "pack",
        help="Convention pack utilities (validate, verify, status, lint)",
    )
    pack_sub = p_pack.add_subparsers(dest="pack_command")
    p_pack_val = pack_sub.add_parser(
        "validate",
        help="Validate a convention pack YAML file",
    )
    p_pack_val.add_argument(
        "file", type=Path,
        help="Pack YAML file to validate",
    )
    p_pack_val.set_defaults(func=_cmd_pack_validate)

    p_pack_verify = pack_sub.add_parser(
        "verify",
        help=(
            "Verify a pack's values_registry pointers (Extension B). "
            "Static inspection by default; pass --fetch to attempt "
            "network fetch and hash check."
        ),
    )
    p_pack_verify.add_argument(
        "file", type=Path,
        help="Pack YAML file to verify",
    )
    p_pack_verify.add_argument(
        "--fetch", action="store_true",
        help=(
            "Fetch each registry pointer's contents and verify hash "
            "(stub in Phase 1; full implementation lands when Phase 2/3 "
            "packs ship)"
        ),
    )
    p_pack_verify.set_defaults(func=_cmd_pack_verify)

    p_pack_status = pack_sub.add_parser(
        "status",
        help=(
            "Show a pack's metadata: license, dimensions, registry "
            "pointers (read-only, no network)"
        ),
    )
    p_pack_status.add_argument(
        "file", type=Path,
        help="Pack YAML file to inspect",
    )
    p_pack_status.set_defaults(func=_cmd_pack_status)

    p_pack_lint = pack_sub.add_parser(
        "lint",
        help=(
            "Lint a pack for non-fatal style issues and upgrade hints "
            "(advisory by default; pass --strict to exit nonzero on "
            "any finding)"
        ),
    )
    p_pack_lint.add_argument(
        "file", type=Path,
        help="Pack YAML file to lint",
    )
    p_pack_lint.add_argument(
        "--strict", action="store_true",
        help="Exit nonzero on any lint finding",
    )
    p_pack_lint.set_defaults(func=_cmd_pack_lint)

    p_pack.set_defaults(func=lambda args: (
        print(
            "Usage: bulla pack <validate|verify|status|lint> FILE",
            file=sys.stderr,
        )
        or sys.exit(1)
    ) if not getattr(args, "pack_command", None) else None)

    # ── merge ─────────────────────────────────────────────────────────
    p_merge = subparsers.add_parser(
        "merge",
        help="Merge vocabularies from multiple receipts (DAG convergence)",
    )
    p_merge.add_argument(
        "receipts", nargs="+", type=Path,
        help="Receipt JSON files to merge (argument order IS precedence order)",
    )
    p_merge.add_argument(
        "--receipt", type=Path, metavar="FILE",
        help="Write merged receipt JSON to file (DAG receipt with parent_receipt_hashes)",
    )
    p_merge.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    p_merge.set_defaults(func=_cmd_merge)

    # ── replay (was: proxy) ────────────────────────────────────────────
    # Renamed 2026-05-17 (live-mcp-proxy sprint). The new `proxy`
    # subcommand is the live stdio MCP proxy; this trace-replayer
    # retains its semantics under the clearer `replay` name. A back-
    # compat alias `proxy` is registered below with a deprecation
    # warning so existing invocations keep working.
    def _add_replay_arguments(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--manifests",
            type=Path,
            required=True,
            metavar="DIR",
            help="Directory of captured MCP manifest JSON files",
        )
        p.add_argument(
            "trace",
            type=Path,
            help="JSON trace file (array or object with 'calls')",
        )
        p.add_argument(
            "--format",
            choices=["text", "json"],
            default="text",
            help="Output format (default: text)",
        )
        p.add_argument(
            "-o", "--output",
            type=Path,
            default=None,
            metavar="FILE",
            help="Write output to file instead of stdout",
        )

    p_replay = subparsers.add_parser(
        "replay",
        help="Replay a composition-aware proxy trace against captured manifests",
    )
    _add_replay_arguments(p_replay)
    p_replay.set_defaults(func=_cmd_proxy)

    # ── proxy (live) ──────────────────────────────────────────────────
    p_proxy = subparsers.add_parser(
        "proxy",
        help=(
            "Run a live MCP proxy. Aggregates N backend MCP servers, "
            "injects bulla__* meta-tools, computes incremental witness "
            "rank. Speaks stdio JSON-RPC to the upstream client. "
            "(For the old trace-replayer use `bulla replay`.)"
        ),
    )
    # Live-proxy mode: positional command lines, no required --manifests.
    p_proxy.add_argument(
        "commands",
        nargs="*",
        metavar="COMMAND",
        help=(
            "Backend MCP server commands (one per backend). Auto-named "
            "server_0, server_1, ... unless --config supplies names."
        ),
    )
    p_proxy.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="FILE",
        help="YAML config: {servers: {name: {command, env}}}",
    )
    p_proxy.add_argument(
        "--telemetry-out",
        type=Path,
        default=None,
        metavar="FILE",
        help="Write per-call telemetry as JSON Lines.",
    )
    p_proxy.add_argument(
        "--inject-prompt",
        action="store_true",
        help=(
            "Print the agent system-prompt fragment to stdout and exit. "
            "Paste this into your agent's system prompt so it knows "
            "how to consult bulla__* meta-tools."
        ),
    )
    # The deed surface: an identity to sign under and a registry to log/verify
    # against. With both, the proxy exposes bulla__deed_emit/verify/lookup.
    p_proxy.add_argument(
        "--key", type=Path, default=None, metavar="FILE",
        help=(
            "ed25519 key file to sign deeds with (run `bulla key gen` first). "
            "Together with a local --registry, enables bulla__deed_emit. "
            "Requires bulla[identity]."
        ),
    )
    p_proxy.add_argument(
        "--issuer", type=str, default=None, metavar="URI",
        help="External issuer URI to bind to (default: the key's did:key).",
    )
    p_proxy.add_argument(
        "--registry", type=str, default=None, metavar="PATH_OR_URL",
        help=(
            "Deed registry: a local JSONL path (read+append) or an http(s) URL "
            "to a `bulla registry serve` endpoint (read-only). Enables "
            "bulla__deed_verify/lookup; a local path also enables emit."
        ),
    )
    p_proxy.add_argument(
        "--enforce", action="store_true",
        help=(
            "ENFORCE mode (OBSERVE -> ENFORCE): refuse a cross-owner tools/call whose "
            "counterparty deed is not authentic + included under a trusted root + "
            "certifying fee=0, BEFORE the backend is touched. The counterparty presents "
            "its cert via the `_bulla_certificate` argument. Default off (advisory)."
        ),
    )
    p_proxy.add_argument(
        "--trusted-root", type=str, default=None, metavar="HASH",
        help=(
            "With --enforce against a remote registry: the root you pin independently "
            "of the host. Absent a pin, a host-asserted root is refused."
        ),
    )
    p_proxy.add_argument(
        "--shadow", action="store_true",
        help=(
            "SHADOW mode (the observe-grade gateway): emit a signed per-call deed — "
            "carrying the v0.2 recourse envelope — for every side-effecting tools/call "
            "(MCP annotations else conservative default: unknown = write). Never blocks; "
            "needs --key and a local --registry, else degrades to telemetry-only."
        ),
    )
    p_proxy.add_argument(
        "--mandate-principal", type=str, default=None, metavar="REF",
        help=(
            "The surviving principal for shadow receipts' authority block (e.g. "
            "did:web:acme.example#ops) — the terminus of the escalate rung."
        ),
    )
    p_proxy.add_argument(
        "--mandate-policy", type=str, default=None, metavar="HASH",
        help="Policy reference (policy@hash) for the shadow receipts' authority block.",
    )
    p_proxy.add_argument(
        "--gate-reads", action="store_true",
        help=(
            "With --enforce: gate ALL calls, including reads. Default gates only "
            "side-effecting calls (the gateway law is 'no unreceipted side effects')."
        ),
    )
    # Legacy fallthrough: support the old `bulla proxy --manifests ...
    # trace.json` invocation for one release cycle by accepting the
    # same flags. If --manifests is supplied, we dispatch the replayer.
    p_proxy.add_argument(
        "--manifests",
        type=Path,
        default=None,
        metavar="DIR",
        help=argparse.SUPPRESS,
    )
    p_proxy.add_argument(
        "--format", choices=["text", "json"], default=None,
        help=argparse.SUPPRESS,
    )
    p_proxy.add_argument(
        "-o", "--output", type=Path, default=None, metavar="FILE",
        help=argparse.SUPPRESS,
    )
    p_proxy.set_defaults(func=_cmd_proxy_dispatch)

    # ── serve ─────────────────────────────────────────────────────────
    p_serve = subparsers.add_parser(
        "serve",
        help="Run as MCP server (stdio transport)",
    )
    p_serve.set_defaults(func=lambda _: _cmd_serve())

    # ── init ──────────────────────────────────────────────────────────
    p_init = subparsers.add_parser(
        "init",
        help="Interactive wizard to generate a composition YAML",
    )
    p_init.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Output file path (default: <name>.yaml)",
    )
    p_init.set_defaults(func=_cmd_init)

    # ── hosts ─────────────────────────────────────────────────────────
    p_hosts = subparsers.add_parser(
        "hosts",
        help="Manage and inspect MCP host integrations",
    )
    hosts_sub = p_hosts.add_subparsers(dest="hosts_command")
    p_hosts_list = hosts_sub.add_parser(
        "list",
        help="List registered MCP hosts and which configs are present on this system",
    )
    p_hosts_list.add_argument(
        "-v", "--verbose", action="store_true",
        help="Show every candidate path scanned per host with a per-path reason "
             "(detected / not present / parse failure / no recognized servers key).",
    )
    p_hosts_list.add_argument(
        "--host", metavar="NAME",
        help="Restrict output to one host (e.g. cline, codex, claude-code).",
    )
    p_hosts_list.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format. Default 'text' is human-readable (see -v for full trace). "
             "'json' emits a structured document with per-path probe data — "
             "stable schema (schema_version: '1') intended for CI / wrapper tooling.",
    )
    p_hosts_list.set_defaults(func=_cmd_hosts_list)
    p_hosts.set_defaults(func=lambda _: _cmd_hosts_list(_))

    # ── frameworks ────────────────────────────────────────────────────
    p_frameworks = subparsers.add_parser(
        "frameworks",
        help="Manage and inspect framework adapters (LangGraph, CrewAI, Anthropic Messages)",
    )
    frameworks_sub = p_frameworks.add_subparsers(dest="frameworks_command")
    p_frameworks_list = frameworks_sub.add_parser(
        "list",
        help="List registered framework adapters and parse-mode support",
    )
    p_frameworks_list.set_defaults(func=_cmd_frameworks_list)
    p_frameworks.set_defaults(func=lambda _: _cmd_frameworks_list(_))

    # ── import ────────────────────────────────────────────────────────
    p_import = subparsers.add_parser(
        "import",
        help="Convert framework-native tool definitions into a Bulla manifest",
    )
    p_import.add_argument(
        "framework",
        help="Framework name (e.g. anthropic-messages, langgraph, crewai). "
             "Use 'bulla frameworks list' to see registered adapters.",
    )
    p_import.add_argument(
        "source",
        help="Source file path, directory, or '-' for stdin",
    )
    p_import.add_argument(
        "--out", type=Path, metavar="FILE",
        help="Write manifest JSON to FILE (default: stdout)",
    )
    p_import.add_argument(
        "--audit", action="store_true",
        help="Pipe through bulla audit immediately",
    )
    p_import.add_argument(
        "--format", choices=["text", "json", "sarif"], default="text",
        help="Audit output format (only relevant with --audit)",
    )
    p_import.add_argument(
        "--mode", choices=["static", "runtime"], default="static",
        help="Parse mode (default: static; runtime reserved for future sprint)",
    )
    p_import.set_defaults(func=_cmd_import)

    # ── experimental predicate invention ─────────────────────────────
    p_experimental = subparsers.add_parser(
        "experimental",
        help="Research-only surfaces (not part of the stable Bulla API)",
    )
    experimental_sub = p_experimental.add_subparsers(dest="experimental_command")
    p_invent = experimental_sub.add_parser(
        "invent",
        help="Synthesize an FRSL-1 predicate package or a checked negative exit",
    )
    p_invent.add_argument("problem", type=Path, help="SeamProblem JSON file")
    p_invent.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write SynthesisResult JSON to this path (default: stdout)",
    )
    p_invent.add_argument(
        "--receipt",
        type=Path,
        help="Also mint an ordinary ActionReceipt with action.type=bulla.invent",
    )
    p_invent.add_argument("--principal", help="Surviving authority principal for --receipt")
    p_invent.add_argument("--policy", help="Pinned authority policy for --receipt")
    p_invent.add_argument("--forum-endpoint", help="Persistent challenge endpoint for --receipt")
    p_invent.add_argument("--forum-root", help="Independently pinned forum root for --receipt")
    p_invent.add_argument("--receipt-scope", help="Prose bounds.scope for --receipt")
    p_invent.add_argument(
        "--challenge-window",
        default="P30D",
        help="Receipt challenge window (default: P30D)",
    )
    p_invent.set_defaults(func=_cmd_experimental_invent)

    p_verify_invention = experimental_sub.add_parser(
        "verify-invention",
        help="Replay package gates and objective failure certificates",
    )
    p_verify_invention.add_argument("problem", type=Path, help="SeamProblem JSON file")
    p_verify_invention.add_argument("result", type=Path, help="SynthesisResult JSON file")
    p_verify_invention.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
    )
    p_verify_invention.set_defaults(func=_cmd_experimental_verify_invention)

    p_explain_invention = experimental_sub.add_parser(
        "explain-invention",
        help="Render a compact human explanation of a synthesis result",
    )
    p_explain_invention.add_argument("result", type=Path, help="SynthesisResult JSON file")
    p_explain_invention.set_defaults(func=_cmd_experimental_explain_invention)

    p_apply_invention = experimental_sub.add_parser(
        "apply-invention",
        help="Apply an independently checked package to one shared-vocabulary structure",
    )
    p_apply_invention.add_argument("problem", type=Path)
    p_apply_invention.add_argument("result", type=Path)
    p_apply_invention.add_argument("structure", type=Path)
    p_apply_invention.add_argument(
        "--argument", action="append", required=True,
        help="Target argument (repeat once per target-predicate argument)",
    )
    p_apply_invention.add_argument("--adapter-version", required=True)
    p_apply_invention.add_argument(
        "--package-hash",
        help="Select one offered package from a CHOICE_REQUIRED result",
    )
    p_apply_invention.set_defaults(func=_cmd_experimental_apply_invention)

    p_select_invention = experimental_sub.add_parser(
        "select-invention",
        help="Govern an offered non-unique package with a signed selection receipt",
    )
    p_select_invention.add_argument("problem", type=Path)
    p_select_invention.add_argument("result", type=Path)
    p_select_invention.add_argument("--package-hash", required=True)
    p_select_invention.add_argument("--principal", required=True)
    p_select_invention.add_argument("--policy", required=True)
    p_select_invention.add_argument("--scope", required=True)
    p_select_invention.add_argument("--key", type=Path, required=True)
    p_select_invention.add_argument("--issuer")
    p_select_invention.add_argument("--timestamp")
    p_select_invention.add_argument("-o", "--output", type=Path, required=True)
    p_select_invention.set_defaults(func=_cmd_experimental_select_invention)

    p_plan_enrichment = experimental_sub.add_parser(
        "plan-enrichment",
        help="Compute exact Pareto-minimal observable plans and a justified request",
    )
    p_plan_enrichment.add_argument("problem", type=Path)
    p_plan_enrichment.add_argument("result", type=Path)
    p_plan_enrichment.add_argument("offers", type=Path)
    p_plan_enrichment.add_argument("--passport", type=Path)
    p_plan_enrichment.add_argument("--manifest", type=Path)
    p_plan_enrichment.add_argument("-o", "--output", type=Path)
    p_plan_enrichment.set_defaults(func=_cmd_experimental_plan_enrichment)

    p_respond_enrichment = experimental_sub.add_parser(
        "respond-enrichment",
        help="Sign CONSENT, REFUSE, COUNTEROFFER, or PROVIDE for one plan",
    )
    p_respond_enrichment.add_argument("packet", type=Path)
    p_respond_enrichment.add_argument(
        "--status",
        required=True,
        choices=("CONSENT", "REFUSE", "COUNTEROFFER", "PROVIDE"),
    )
    p_respond_enrichment.add_argument("--plan-hash")
    p_respond_enrichment.add_argument("--facts", type=Path)
    p_respond_enrichment.add_argument("--counteroffers", type=Path)
    p_respond_enrichment.add_argument("--reason")
    p_respond_enrichment.add_argument("--key", type=Path, required=True)
    p_respond_enrichment.add_argument("--issuer")
    p_respond_enrichment.add_argument("-o", "--output", type=Path)
    p_respond_enrichment.set_defaults(func=_cmd_experimental_respond_enrichment)

    p_refine_envelope = experimental_sub.add_parser(
        "refine-envelope",
        help="Admit consented evidence and emit a monotone refinement certificate",
    )
    p_refine_envelope.add_argument("problem", type=Path)
    p_refine_envelope.add_argument("prior_result", type=Path)
    p_refine_envelope.add_argument("packet", type=Path)
    p_refine_envelope.add_argument("--response", type=Path, action="append", required=True)
    p_refine_envelope.add_argument("--plan-hash", required=True)
    p_refine_envelope.add_argument("-o", "--output", type=Path)
    p_refine_envelope.set_defaults(func=_cmd_experimental_refine_envelope)

    p_verify_refinement = experimental_sub.add_parser(
        "verify-refinement",
        help="Replay state inclusion and all envelope-refinement gates",
    )
    p_verify_refinement.add_argument("bundle", type=Path)
    p_verify_refinement.set_defaults(func=_cmd_experimental_verify_refinement)

    p_assess_finality = experimental_sub.add_parser(
        "assess-finality",
        help="Apply the experimental closure/authority/reserve finality controller",
    )
    p_assess_finality.add_argument("case", type=Path)
    p_assess_finality.add_argument("-o", "--output", type=Path)
    p_assess_finality.set_defaults(func=_cmd_experimental_assess_finality)

    p_explain_finality = experimental_sub.add_parser(
        "explain-finality",
        help="Emit or replay typed minimal blockers and sufficient finality routes",
    )
    p_explain_finality.add_argument("case", type=Path)
    p_explain_finality.add_argument(
        "--verify",
        type=Path,
        help="Replay and compare a previously emitted explanation",
    )
    p_explain_finality.add_argument("-o", "--output", type=Path)
    p_explain_finality.set_defaults(func=_cmd_experimental_explain_finality)

    p_repair_reliance = experimental_sub.add_parser(
        "repair-reliance",
        help="Enumerate exact inclusion-minimal repairs in a declared finite catalog",
    )
    p_repair_reliance.add_argument("verification", type=Path)
    p_repair_reliance.add_argument("policy", type=Path)
    p_repair_reliance.add_argument("catalog", type=Path)
    p_repair_reliance.add_argument("-o", "--output", type=Path)
    p_repair_reliance.set_defaults(func=_cmd_experimental_repair_reliance)

    p_check_candidate = experimental_sub.add_parser(
        "check-candidate",
        help="Gate an external FRSL-1 proposal under an explicit disclosure budget",
    )
    p_check_candidate.add_argument("problem", type=Path)
    p_check_candidate.add_argument("candidate", type=Path)
    p_check_candidate.add_argument("budget", type=Path)
    p_check_candidate.add_argument("--generator", required=True)
    p_check_candidate.add_argument("--generator-version", required=True)
    p_check_candidate.add_argument("--prompt-hash", required=True)
    p_check_candidate.add_argument("--attempt", type=int, default=1)
    p_check_candidate.add_argument("--emitted-countermodels", type=int, default=0)
    p_check_candidate.set_defaults(func=_cmd_experimental_check_candidate)

    p_checkpoint = experimental_sub.add_parser(
        "checkpoint",
        help="Issue or verify a signed append-only witness checkpoint",
    )
    checkpoint_sub = p_checkpoint.add_subparsers(dest="checkpoint_command")
    p_checkpoint_issue = checkpoint_sub.add_parser("issue", help="Sign the current local log head")
    p_checkpoint_issue.add_argument("--registry", type=Path, required=True)
    p_checkpoint_issue.add_argument("--log-id", required=True)
    p_checkpoint_issue.add_argument("--key", type=Path, required=True)
    p_checkpoint_issue.add_argument("--issuer")
    p_checkpoint_issue.add_argument("--previous", type=Path)
    p_checkpoint_issue.add_argument("--archive", type=Path)
    p_checkpoint_issue.add_argument("--consistency-output", type=Path)
    p_checkpoint_issue.add_argument("--issued-at")
    p_checkpoint_issue.add_argument("-o", "--output", type=Path, required=True)
    p_checkpoint_issue.set_defaults(func=_cmd_experimental_checkpoint_issue)
    p_checkpoint_verify = checkpoint_sub.add_parser("verify", help="Verify a head and optional extension")
    p_checkpoint_verify.add_argument("checkpoint", type=Path)
    p_checkpoint_verify.add_argument("--previous", type=Path)
    p_checkpoint_verify.add_argument("--consistency", type=Path)
    p_checkpoint_verify.set_defaults(func=_cmd_experimental_checkpoint_verify)
    p_checkpoint_serve = checkpoint_sub.add_parser(
        "serve", help="Serve latest, history, and adjacent consistency read-only"
    )
    p_checkpoint_serve.add_argument("archive", type=Path)
    p_checkpoint_serve.add_argument("--host", default="127.0.0.1")
    p_checkpoint_serve.add_argument("--port", type=int, default=0)
    p_checkpoint_serve.set_defaults(func=_cmd_experimental_checkpoint_serve)
    p_experimental.set_defaults(func=_cmd_experimental)

    # ── showcase ────────────────────────────────────────────────────────
    p_showcase = subparsers.add_parser(
        "showcase",
        help="Run the full algebraic repair loop demo on bundled MCP manifests",
    )
    p_showcase.add_argument(
        "--json", action="store_true",
        help="Emit machine-readable JSON instead of human-readable output",
    )
    p_showcase.set_defaults(func=_cmd_showcase)

    args = parser.parse_args()

    if not args.command:
        print(f"bulla {__version__} — witness kernel for agentic compositions\n")
        print("Quick start:")
        print("  bulla audit                    # audit all MCP servers in your config")
        print("  bulla audit --discover         # audit with LLM convention discovery")
        print("  bulla audit --discover --receipt r.json  # audit + discovery + receipt")
        print("  bulla audit --guided-discover  # obligation-directed repair via LLM")
        print("  bulla audit --converge         # iterative convergence loop")
        print("  bulla audit --chain r.json     # inherit prior vocabulary (CI mode)")
        print("  bulla merge a.json b.json --receipt m.json  # merge receipt vocabularies (DAG)")
        print("  bulla gauge tools.json         # diagnose manifest with disclosure set")
        print("  bulla gauge --mcp-server 'python -m my_server'  # diagnose live server")
        print("  bulla discover --manifests DIR -o found.yaml  # LLM dimension discovery")
        print("  bulla diagnose --examples      # try bundled compositions")
        print("  bulla diagnose my-comp.yaml    # diagnose your own")
        print("  bulla check compositions/      # CI gate (exit 1 on blind spots)")
        print("  bulla bridge comp.yaml -o bridged.yaml  # auto-bridge blind spots")
        print("  bulla proxy --manifests DIR trace.json  # replay proxy trace")
        print("  bulla witness comp.yaml        # emit witness receipt (JSON)")
        print("  bulla showcase                 # full algebraic repair loop demo")
        print("  bulla serve                    # run as MCP server (stdio)")
        print("  bulla manifest --from-json tools.json  # generate manifests")
        print("  bulla pack validate pack.yaml  # validate a convention pack")
        print("  bulla init                     # interactive composition wizard")
        print()
        print("Run `bulla <command> --help` for details.")
        sys.exit(0)

    args.func(args)
