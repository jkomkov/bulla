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

    witness = getattr(args, "witness", False)
    diagnostics: list[tuple] = []
    for path in paths:
        try:
            comp = load_composition(path)
            diag = diagnose(comp, include_witness_geometry=witness)
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

    witness = getattr(args, "witness", False)
    diagnostics: list[tuple] = []
    for path in paths:
        try:
            comp = load_composition(path)
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

    if repair_geometry is not None:
        er = repair_geometry.epistemic_view()
        lines.append("")
        lines.append(f"  Repair regime:  {er.regime}")
        if er.regime == "exact":
            lines.append(f"    Geometry dividend: {er.geometry_dividend}  (provably optimal)")
        else:
            lines.append(f"    Geometry dividend: {er.geometry_dividend}  (approximation)")
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
    repair_geometry: "RepairGeometry | None" = None,
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
                        "Error: No MCP config detected. Pass a config path "
                        "or run 'bulla hosts list' to see supported hosts.",
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
    else:
        audit_results = results

    if fmt == "json":
        print(_audit_json(
            audit_results, diag, disclosure, basis, decomposition,
            own_obligations=own_obligations or None,
            obligation_check=obligation_check,
            guided_repair=guided_repair_report,
            repair_geometry=repair_geo,
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
            "Witness kernel for agentic compositions. "
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
    p_diag.add_argument(
        "--witness",
        action="store_true",
        help=(
            "Include witness-geometry diagnostics (leverage scores, "
            "N_eff concentration, coloops/loops, greedy minimum-cost "
            "disclosure basis). Computed only when fee > 0."
        ),
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

    # ── proxy ─────────────────────────────────────────────────────────
    p_proxy = subparsers.add_parser(
        "proxy",
        help="Replay a composition-aware proxy trace against captured manifests",
    )
    p_proxy.add_argument(
        "--manifests",
        type=Path,
        required=True,
        metavar="DIR",
        help="Directory of captured MCP manifest JSON files",
    )
    p_proxy.add_argument(
        "trace",
        type=Path,
        help="JSON trace file (array or object with 'calls')",
    )
    p_proxy.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    p_proxy.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        metavar="FILE",
        help="Write output to file instead of stdout",
    )
    p_proxy.set_defaults(func=_cmd_proxy)

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
        print("  bulla serve                    # run as MCP server (stdio)")
        print("  bulla manifest --from-json tools.json  # generate manifests")
        print("  bulla pack validate pack.yaml  # validate a convention pack")
        print("  bulla init                     # interactive composition wizard")
        print()
        print("Run `bulla <command> --help` for details.")
        sys.exit(0)

    args.func(args)
