"""CLI entry point: diagnose and check subcommands."""

from __future__ import annotations

import argparse
import importlib.resources
import json
import sys
from pathlib import Path

import yaml

from bulla import __version__
from bulla.diagnostic import diagnose
from bulla.formatters import format_json, format_sarif, format_text
from bulla.parser import CompositionError, load_composition


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

    diagnostics: list[tuple] = []
    for path in paths:
        try:
            comp = load_composition(path)
            diag = diagnose(comp)
            diagnostics.append((diag, path))
        except CompositionError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error processing {path}: {e}", file=sys.stderr)
            sys.exit(1)

    fmt = getattr(args, "format", "text")
    brief = getattr(args, "brief", False)

    if fmt == "sarif":
        print(format_sarif(diagnostics))
    elif fmt == "json":
        if len(diagnostics) == 1:
            print(format_json(diagnostics[0][0], diagnostics[0][1]))
        else:
            combined = [
                json.loads(format_json(d, p)) for d, p in diagnostics
            ]
            print(json.dumps(combined, indent=2))
    elif brief:
        for diag, path in diagnostics:
            bs = len(diag.blind_spots)
            status = "PASS" if diag.coherence_fee == 0 else "FAIL"
            print(
                f"  {status}  {path.name}  "
                f"blind_spots={bs}  fee={diag.coherence_fee}"
            )
        print()
    else:
        sep = "\u2500" * 60
        for i, (d, _p) in enumerate(diagnostics):
            if i > 0:
                print(sep)
            print(format_text(d))

        if len(diagnostics) > 1:
            print("\u2501" * 60)
            fees = [d.coherence_fee for d, _ in diagnostics]
            total_bs = sum(len(d.blind_spots) for d, _ in diagnostics)
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

    diagnostics: list[tuple] = []
    for path in paths:
        try:
            comp = load_composition(path)
            diag = diagnose(comp)
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

    fmt = getattr(args, "format", "text")
    if fmt == "sarif":
        print(format_sarif(diagnostics))
    elif fmt == "json":
        combined = [json.loads(format_json(d, p)) for d, p in diagnostics]
        result = {
            "passed": not failed,
            "max_blind_spots": args.max_blind_spots,
            "max_unbridged": args.max_unbridged,
            "compositions": combined,
        }
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
        print()
        if failed:
            print("  Result: FAIL")
            print()
            print("  Run `bulla diagnose <file>` for full details.")
        else:
            print("  Result: PASS")
        print()

    sys.exit(1 if failed else 0)


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


def _cmd_bridge(args: argparse.Namespace) -> None:
    """Generate bridged composition YAML or JSON patches from a diagnosed composition."""
    paths = _resolve_paths(args.files)
    if not paths:
        print("No composition files found.", file=sys.stderr)
        sys.exit(1)

    for path in paths:
        try:
            comp = load_composition(path)
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
            comp = load_composition(path)
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
) -> None:
    violations: list[str] = []
    max_fee = getattr(args, "max_fee", None)
    max_bs = getattr(args, "max_blind_spots", None)
    max_unmet = getattr(args, "max_unmet", None)
    max_contradictions = getattr(args, "max_contradictions", None)
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

    diag = guard.diagnose()

    from bulla.diagnostic import prescriptive_disclosure
    disclosure = prescriptive_disclosure(guard.composition, diag.coherence_fee)

    fmt = getattr(args, "format", "text")
    verbose = getattr(args, "verbose", False)
    if fmt == "json":
        print(_gauge_json(diag, disclosure, guard.witness_basis))
    elif fmt == "sarif":
        print(guard.to_sarif())
    else:
        print(_gauge_text(diag, disclosure, guard.witness_basis, verbose=verbose))

    output_comp = getattr(args, "output_composition", None)
    if output_comp:
        guard.to_yaml(output_comp)
        print(f"Wrote composition to {output_comp}", file=sys.stderr)

    _gauge_threshold_check(diag, args)


# ── audit ─────────────────────────────────────────────────────────────


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
) -> str:
    ok_count = sum(1 for r in server_results if r.ok)
    fail_count = sum(1 for r in server_results if not r.ok)

    lines: list[str] = []
    skip_label = f" ({fail_count} skipped)" if fail_count else ""
    lines.append(f"  bulla audit: {ok_count} server(s) scanned{skip_label}")
    lines.append("")

    lines.append("  Servers:")
    for r in server_results:
        if r.ok:
            lines.append(f"    {r.name:<20s} {len(r.tools):>3} tools   OK")
        else:
            short_err = r.error.split("\n")[0][:60] if r.error else "unknown"
            lines.append(f"    {r.name:<20s}  --        FAILED: {short_err}")
    lines.append("")

    if ok_count == 0:
        lines.append("  No servers scanned successfully.")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"  Combined composition: {diag.n_tools} tools, {diag.n_edges} edges")
    lines.append("")
    lines.append(f"  Coherence fee:  {diag.coherence_fee}")
    lines.append(f"  Blind spots:    {len(diag.blind_spots)}")
    lines.append(f"  Bridges:        {len(diag.bridges)}")

    if decomposition and ok_count > 1:
        intra = sum(decomposition.local_fees)
        lines.append("")
        lines.append("  Cross-server risk:")
        lines.append(f"    Intra-server fee:  {intra}  (blind spots within individual servers)")
        lines.append(f"    Boundary fee:      {decomposition.boundary_fee}  (blind spots between servers)")

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
        lines.append(f"  Obligations ({len(own_obligations)} from boundary_fee={bf}):")
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
        lines.append(f"    Met: {obligation_check['met']}"
                      + (f" ({met_dims})" if met_dims else ""))
        lines.append(f"    Unmet: {obligation_check['unmet']}"
                      + (f" ({unmet_dims})" if unmet_dims else ""))
        lines.append(f"    Irrelevant: {obligation_check['irrelevant']}")

    if guided_repair:
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
            n_vals = sum(
                len(d.get("known_values", [])) for d in disc_dims.values()
            )
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
                    max_prefix = max(
                        (len(t.split("__")[0]) for t in tv), default=0
                    )
                    for tool_name, value in tv.items():
                        prefix = tool_name.split("__")[0]
                        lines.append(
                            f"      {prefix:<{max_prefix}s}: {value}"
                        )
                else:
                    tools = ddef.get("provenance", {}).get("source_tools", [])
                    tool_str = f" (from {', '.join(tools)})" if tools else ""
                    lines.append(f"    {dname}: {', '.join(vals)}{tool_str}")

            if contradictions:
                lines.append(
                    f"  {len(contradictions)} convention mismatch(es) across server boundaries"
                )

    lines.append("")
    return "\n".join(lines)


def _audit_json(
    server_results: list,
    diag: "Diagnostic",
    disclosure: list[tuple[str, str]],
    basis: "WitnessBasis | None",
    decomposition: "FeeDecomposition | None",
    own_obligations: tuple | None = None,
    obligation_check: dict | None = None,
    guided_repair: dict | None = None,
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
        from bulla.config import ConfigError, find_mcp_config, parse_mcp_config
        from bulla.scan import scan_mcp_servers_parallel

        if config_path is None:
            config_path = find_mcp_config()
            if config_path is None:
                print(
                    "Error: No MCP config found. Provide a config file path or "
                    "create ~/.cursor/mcp.json",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(f"Auto-detected config: {config_path}", file=sys.stderr)

        try:
            entries = parse_mcp_config(config_path)
        except ConfigError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        if not entries:
            print("Error: No stdio MCP servers found in config.", file=sys.stderr)
            sys.exit(1)

        servers_cfg = {
            e.name: {"command": e.command, "env": e.env or None}
            for e in entries
        }
        results = scan_mcp_servers_parallel(servers_cfg)

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

    guard = BullaGuard.from_tools_list(all_tools, name="audit")
    comp = guard.composition
    basis = guard.witness_basis

    tool_to_server = {
        t.name: t.name.split("__")[0] for t in comp.tools
    }

    from bulla.diagnostic import diagnose
    diag = diagnose(comp)
    disclosure = prescriptive_disclosure(comp, diag.coherence_fee)

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

    fmt = getattr(args, "format", "text")
    verbose = getattr(args, "verbose", False)

    if manifests_dir:
        from types import SimpleNamespace
        audit_results = []
        for sname in server_names:
            n_tools = sum(1 for t in comp.tools if t.name.startswith(f"{sname}__"))
            audit_results.append(SimpleNamespace(name=sname, ok=True, tools=[None] * n_tools, error=None))
    else:
        audit_results = results

    if fmt == "json":
        print(_audit_json(
            audit_results, diag, disclosure, basis, decomposition,
            own_obligations=own_obligations or None,
            obligation_check=obligation_check,
            guided_repair=guided_repair_report,
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


def _cmd_scan(args: argparse.Namespace) -> None:
    _configure_packs_from_args(args)
    from bulla.guard import BullaGuard
    from bulla.scan import ScanError, scan_mcp_server, scan_mcp_servers

    try:
        if len(args.commands) == 1:
            guard = BullaGuard.from_mcp_server(args.commands[0])
        else:
            tools = scan_mcp_servers(args.commands)
            guard = BullaGuard.from_tools_list(tools, name="multi-server-scan")
    except ScanError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    diag = guard.diagnose()
    fmt = getattr(args, "format", "text")

    if fmt == "json":
        print(guard.to_json())
    elif fmt == "sarif":
        print(guard.to_sarif())
    else:
        print(guard.to_text())

    if args.output:
        guard.to_yaml(args.output)
        print(f"Wrote composition to {args.output}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="bulla",
        description=(
            "Witness kernel for agent tool compositions. "
            "Diagnoses blind spots invisible to bilateral verification, "
            "attests to composition integrity, and recommends bridge annotations."
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
    _add_pack_args(p_diag)
    p_diag.set_defaults(func=_cmd_diagnose)

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
    _add_pack_args(p_check)
    p_check.set_defaults(func=_cmd_check)

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
        help="Scan live MCP server(s) via stdio and diagnose",
    )
    p_scan.add_argument(
        "commands", nargs="+",
        help="Shell command(s) to start MCP server(s)",
    )
    p_scan.add_argument(
        "--format",
        choices=["text", "json", "sarif"],
        default="text",
        help="Output format (default: text)",
    )
    p_scan.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Save inferred composition YAML to file",
    )
    _add_pack_args(p_scan)
    p_scan.set_defaults(func=_cmd_scan)

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
        help="Exit 1 if contradictions exceed N (CI gating)",
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
        help="Convention pack utilities (validate, inspect)",
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
    p_pack.set_defaults(func=lambda args: (
        print("Usage: bulla pack <validate> FILE", file=sys.stderr)
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

    args = parser.parse_args()

    if not args.command:
        print(f"bulla {__version__} — witness kernel for agent tool compositions\n")
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
        print("  bulla witness comp.yaml        # emit witness receipt (JSON)")
        print("  bulla serve                    # run as MCP server (stdio)")
        print("  bulla manifest --from-json tools.json  # generate manifests")
        print("  bulla pack validate pack.yaml  # validate a convention pack")
        print("  bulla init                     # interactive composition wizard")
        print()
        print("Run `bulla <command> --help` for details.")
        sys.exit(0)

    args.func(args)
