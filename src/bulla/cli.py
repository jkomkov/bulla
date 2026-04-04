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
        lines.append(
            f"  Witness basis: {basis.declared} declared, "
            f"{basis.inferred} inferred, {basis.unknown} unknown"
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
    diag: "Diagnostic", args: argparse.Namespace
) -> None:
    violations: list[str] = []
    max_fee = getattr(args, "max_fee", None)
    max_bs = getattr(args, "max_blind_spots", None)
    if max_fee is not None and diag.coherence_fee > max_fee:
        violations.append(
            f"fee {diag.coherence_fee} exceeds --max-fee {max_fee}"
        )
    if max_bs is not None and len(diag.blind_spots) > max_bs:
        violations.append(
            f"{len(diag.blind_spots)} blind spot(s) exceeds "
            f"--max-blind-spots {max_bs}"
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


def _cmd_scan(args: argparse.Namespace) -> None:
    _configure_packs_from_args(args)
    from bulla.guard import BullaGuard
    from bulla.scan import ScanError, scan_mcp_server, scan_mcp_servers

    try:
        if len(args.commands) == 1:
            guard = BullaGuard.from_mcp_server(args.commands[0])
        else:
            from bulla.guard import _composition_from_mcp_tools
            tools = scan_mcp_servers(args.commands)
            comp, basis = _composition_from_mcp_tools(tools, name="multi-server-scan")
            guard = BullaGuard(comp, witness_basis=basis)
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
        print("  bulla gauge tools.json         # diagnose manifest with disclosure set")
        print("  bulla gauge --mcp-server 'python -m my_server'  # diagnose live server")
        print("  bulla diagnose --examples      # try bundled compositions")
        print("  bulla diagnose my-comp.yaml    # diagnose your own")
        print("  bulla check compositions/      # CI gate (exit 1 on blind spots)")
        print("  bulla bridge comp.yaml -o bridged.yaml  # auto-bridge blind spots")
        print("  bulla witness comp.yaml        # emit witness receipt (JSON)")
        print("  bulla serve                    # run as MCP server (stdio)")
        print("  bulla manifest --from-json tools.json  # generate manifests")
        print("  bulla init                     # interactive composition wizard")
        print()
        print("Run `bulla <command> --help` for details.")
        sys.exit(0)

    args.func(args)
