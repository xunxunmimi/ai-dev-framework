#!/usr/bin/env python3
"""Bind explicit user approval to the current migration plan digest."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from check_discovery_gate import validate_workspace


def digest(workspace: Path) -> tuple[str, dict]:
    plan = workspace / "ir/migration-plan.json"
    if not plan.is_file() or not plan.read_bytes().strip():
        raise SystemExit("migration plan is missing")
    try:
        data = json.loads(plan.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SystemExit(f"migration plan is invalid JSON: {error}") from error
    return hashlib.sha256(plan.read_bytes()).hexdigest(), data


def discovery_binding(plan: dict, report: dict) -> list[str]:
    errors = list(report.get("errors", []))
    binding = plan.get("discovery", {})
    if binding.get("baseline") != "ir/discovery-baseline.json":
        errors.append("plan discovery.baseline must be ir/discovery-baseline.json")
    if binding.get("baselineSha256") != report.get("baselineSha256"):
        errors.append("plan discovery.baselineSha256 does not match the validated baseline")
    if binding.get("sourceIndexSha256") != report.get("sourceIndexSha256"):
        errors.append("plan discovery.sourceIndexSha256 does not match the validated source index")
    return errors


def approval_status(workspace: Path) -> tuple[bool, str]:
    record_path = workspace / "reports/plan-approval.json"
    try:
        current, plan = digest(workspace)
    except SystemExit as error:
        return False, str(error)
    discovery = validate_workspace(workspace)
    errors = discovery_binding(plan, discovery)
    if discovery.get("status") != "passed" or errors:
        return False, "Phase 0 discovery gate or plan binding is incomplete"
    if not record_path.is_file():
        return False, "plan is not approved"
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return False, f"approval record is invalid: {error}"
    valid = (
        record.get("status") == "approved"
        and record.get("planSha256") == current
        and record.get("discoveryBaselineSha256") == discovery["baselineSha256"]
        and record.get("sourceIndexSha256") == discovery["sourceIndexSha256"]
    )
    return valid, "approved" if valid else "approval invalidated by plan, discovery baseline, or source change"


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    approve = sub.add_parser("approve")
    approve.add_argument("workspace", type=Path)
    approve.add_argument("--approved-by", required=True)
    check = sub.add_parser("check")
    check.add_argument("workspace", type=Path)
    args = parser.parse_args()
    workspace = args.workspace.expanduser().resolve()
    record_path = workspace / "reports/plan-approval.json"
    current, plan = digest(workspace)
    discovery = validate_workspace(workspace)
    binding_errors = discovery_binding(plan, discovery)
    if discovery.get("status") != "passed" or binding_errors:
        print(json.dumps({
            "status": "blocked",
            "reason": "Phase 0 discovery gate or plan binding is incomplete",
            "errors": binding_errors,
        }, ensure_ascii=False, indent=2))
        return 1
    if args.command == "approve":
        record = {
            "schemaVersion": "2.0",
            "status": "approved",
            "approvedBy": args.approved_by,
            "approvedAt": datetime.now(timezone.utc).isoformat(),
            "planSha256": current,
            "discoveryBaselineSha256": discovery["baselineSha256"],
            "sourceIndexSha256": discovery["sourceIndexSha256"],
        }
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(current)
        return 0
    valid, message = approval_status(workspace)
    print(message)
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
