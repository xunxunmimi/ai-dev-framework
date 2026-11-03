#!/usr/bin/env python3
"""Validate the Phase 0 discovery baseline before migration-plan approval."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from analyze_source import REQUIRED_AREAS, build_source_index


AREA_DETAIL_REQUIREMENTS = {
    "projectStructure": ("targets", "entryPoints", "buildConfigurations"),
    "architectureAndModules": ("responsibility", "dependencies"),
    "screensAndNavigation": ("states", "navigation"),
    "interactionsAndStates": ("triggers", "states", "failureAndRecovery"),
    "featuresAndBusinessFlows": ("entry", "success", "failure", "recovery"),
    "dependenciesAndLicenses": ("version", "purpose", "license", "targetReplacement"),
    "dataStorageAndMigration": ("storage", "schema", "migration", "security"),
    "networkAndBackendContracts": ("contracts", "auth", "errors", "retryAndCancellation"),
    "permissionsAndSystemCapabilities": ("capability", "trigger", "denialAndRecovery"),
    "resourcesLocalizationAccessibility": ("resources", "localization", "accessibility"),
    "testsBuildSigningRelease": ("build", "tests", "signingAndRelease"),
}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_workspace(workspace: Path) -> dict:
    baseline_path = workspace / "ir/discovery-baseline.json"
    index_path = workspace / "ir/source-index.json"
    errors: list[str] = []
    if not baseline_path.is_file():
        return {"status": "blocked", "errors": ["discovery baseline is missing"]}
    if not index_path.is_file():
        return {"status": "blocked", "errors": ["source index is missing"]}

    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {"status": "blocked", "errors": [f"invalid discovery JSON: {error}"]}

    baseline_digest = file_sha256(baseline_path)
    index_digest = file_sha256(index_path)
    source = baseline.get("source", {})
    if baseline.get("status") != "ready-for-plan":
        errors.append("discovery baseline status must be ready-for-plan")
    if baseline.get("schemaVersion") != "2.2":
        errors.append("discovery baseline schemaVersion must be 2.2")
    if index.get("schemaVersion") != "2.2":
        errors.append("source index schemaVersion must be 2.2")
    if source.get("currentSourceIndexSha256") != index_digest:
        errors.append("currentSourceIndexSha256 does not match source-index.json")
    if source.get("analysedSourceIndexSha256") != index_digest:
        errors.append("source index has not been fully analysed or reconciled")
    if source.get("root") != index.get("sourceRoot"):
        errors.append("discovery source root does not match source index")
    source_root = Path(str(index.get("sourceRoot", "")))
    if not source_root.is_dir():
        errors.append("indexed iOS source root is unavailable")
    else:
        current_index = build_source_index(source_root)
        if current_index.get("sourceFingerprint") != index.get("sourceFingerprint"):
            errors.append("iOS source changed after indexing; rerun analyze_source.py and reconcile affected areas")

    coverage = baseline.get("analysisCoverage", {})
    indexed = index.get("totalFiles")
    if coverage.get("sourceFilesIndexed") != indexed:
        errors.append("sourceFilesIndexed does not match source index")
    if coverage.get("sourceFilesAccountedFor") != indexed:
        errors.append("not every indexed source file is accounted for")

    areas = baseline.get("areas", {})
    for area_name in REQUIRED_AREAS:
        area = areas.get(area_name)
        if not isinstance(area, dict):
            errors.append(f"{area_name}: required analysis area is missing")
            continue
        status = area.get("status")
        evidence = area.get("evidenceRefs", [])
        if status == "not_applicable":
            if not str(area.get("notApplicableReason", "")).strip():
                errors.append(f"{area_name}: not_applicable requires a reason")
            if not evidence:
                errors.append(f"{area_name}: not_applicable requires evidence")
            continue
        if status != "complete":
            errors.append(f"{area_name}: status must be complete or not_applicable")
            continue
        if not str(area.get("summary", "")).strip():
            errors.append(f"{area_name}: complete analysis requires a summary")
        if not evidence:
            errors.append(f"{area_name}: complete analysis requires evidenceRefs")

        items = area.get("items", [])
        if not isinstance(items, list) or not items:
            errors.append(f"{area_name}: complete analysis requires at least one item")
            items = []
        counts = {"mapped": 0, "excluded": 0, "unknown": 0}
        ids: set[str] = set()
        for position, item in enumerate(items):
            prefix = f"{area_name}.items[{position}]"
            item_id = str(item.get("id", "")).strip()
            if not item_id or item_id in ids:
                errors.append(f"{prefix}: id is missing or duplicated")
            ids.add(item_id)
            if not str(item.get("name", "")).strip():
                errors.append(f"{prefix}: name is required")
            if not item.get("sourceRefs"):
                errors.append(f"{prefix}: sourceRefs are required")
            disposition = item.get("disposition")
            if disposition not in counts:
                errors.append(f"{prefix}: disposition must be mapped, excluded, or unknown")
                continue
            counts[disposition] += 1
            if disposition == "mapped" and not str(item.get("targetStrategy", "")).strip():
                errors.append(f"{prefix}: mapped item requires targetStrategy")
            if disposition == "mapped":
                details = item.get("details", {})
                if not isinstance(details, dict):
                    details = {}
                for field in AREA_DETAIL_REQUIREMENTS[area_name]:
                    value = details.get(field)
                    if value is None or value == "" or value == [] or value == {}:
                        errors.append(f"{prefix}: mapped item details.{field} is required")
            if disposition == "excluded" and not str(item.get("reason", "")).strip():
                errors.append(f"{prefix}: excluded item requires reason")
            if disposition == "unknown":
                severity = item.get("severity")
                if severity == "blocking":
                    errors.append(f"{prefix}: blocking unknown must be resolved before migration")
                elif severity == "non_blocking":
                    if not str(item.get("resolutionSlice", "")).strip():
                        errors.append(f"{prefix}: non_blocking unknown requires resolutionSlice")
                else:
                    errors.append(f"{prefix}: unknown requires blocking or non_blocking severity")

        area_coverage = area.get("coverage", {})
        expected = {
            "identified": len(items),
            "mapped": counts["mapped"],
            "excluded": counts["excluded"],
            "unknown": counts["unknown"],
        }
        if area_coverage != expected:
            errors.append(f"{area_name}: coverage must equal actual item dispositions {expected}")

    report = {
        "schemaVersion": "2.2",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if not errors else "blocked",
        "baseline": str(baseline_path),
        "baselineSha256": baseline_digest,
        "sourceIndex": str(index_path),
        "sourceIndexSha256": index_digest,
        "sourceFilesIndexed": index.get("totalFiles"),
        "errors": errors,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    workspace = args.workspace.expanduser().resolve()
    report = validate_workspace(workspace)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
