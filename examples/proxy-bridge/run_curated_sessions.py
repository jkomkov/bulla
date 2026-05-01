"""Run the five curated proxy calibration traces."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bulla.proxy import BullaProxySession

BASE_DIR = Path(__file__).resolve().parent
MANIFESTS_DIR = BASE_DIR.parent / "real_world_audit" / "manifests"
TRACE_DIR = BASE_DIR / "traces"
TRACE_FILES = [
    "clean_memory_puppeteer.json",
    "broken_filesystem_github_path.json",
    "uncertain_github_paging.json",
    "uncertain_github_issue_pull.json",
    "uncertain_filesystem_github_content.json",
]


def _load_manifest_dir(manifests_dir: Path) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(manifests_dir.glob("*.json")):
        data = json.loads(path.read_text())
        tools = data.get("tools", []) if isinstance(data, dict) else data
        if isinstance(tools, list):
            result[path.stem] = tools
    return result


def run_curated_sessions() -> dict[str, Any]:
    manifests = _load_manifest_dir(MANIFESTS_DIR)
    sessions: list[dict[str, Any]] = []
    for filename in TRACE_FILES:
        trace = json.loads((TRACE_DIR / filename).read_text())
        trace_servers = {
            item["server"]
            for item in trace["calls"]
            if isinstance(item, dict) and "server" in item
        }
        session = BullaProxySession(
            {name: tools for name, tools in manifests.items() if name in trace_servers}
        )
        records = session.replay_trace(trace["calls"])
        final_local = records[-1].local_diagnostic
        has_signal = (
            final_local.coherence_fee > 0
            or bool(session.flow_conflicts)
        )
        sessions.append(
            {
                "name": trace["name"],
                "expectation": trace["expectation"],
                "final_local_diagnostic": final_local.to_dict(),
                "flow_conflict_count": len(session.flow_conflicts),
                "has_signal": has_signal,
                "disposition": session.current_receipt.disposition.value,
            }
        )

    report = {
        "manifest_dir": str(MANIFESTS_DIR),
        "sessions": sessions,
    }
    out_path = BASE_DIR / "curated_session_report.json"
    out_path.write_text(json.dumps(report, indent=2))
    return report


def main() -> None:
    report = run_curated_sessions()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
