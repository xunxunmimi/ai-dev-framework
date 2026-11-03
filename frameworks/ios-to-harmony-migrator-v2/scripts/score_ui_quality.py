#!/usr/bin/env python3
"""Calculate an evidence-backed, salience-weighted UI fidelity score."""

import argparse
import json
from pathlib import Path

from ui_state_manifest import validate as validate_ui_states


WEIGHTS = {
    "structure": 25,
    "typography_assets": 20,
    "interaction_states": 20,
    "motion_lifecycle": 15,
    "navigation_restore": 10,
    "geometry_detail": 10,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--state-manifest", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=90)
    parser.add_argument('--expected', type=Path)
    parser.add_argument('--project', type=Path)
    parser.add_argument('--task-id')
    parser.add_argument('--profile', default='T3')
    parser.add_argument('--slice')
    args = parser.parse_args()
    if not (args.project and args.task_id and args.expected):
        report = {'status': 'blocked', 'problems': ['current task binding and independent approved coverage required; legacy scores are unverified']}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    data = {}
    state_manifest = args.state_manifest.expanduser().absolute()
    state_data = {}
    current, expected = None, None
    if args.project and args.task_id and args.expected:
        from read_boundary import require_complete, task_read_scope
        from ui_evidence import load_context, argument_path
        project = args.project.resolve()
        state_manifest = argument_path(args.state_manifest, args.project, project)
        input_path = argument_path(args.input, args.project, project)
        expected_path = argument_path(args.expected, args.project, project)
        with task_read_scope(project, args.task_id):
            boundary = require_complete(project)
            data = boundary.read_json(input_path.relative_to(project).as_posix())
            state_data = boundary.read_json(state_manifest.relative_to(project).as_posix())
            current, expected = load_context(project, args.task_id, args.profile, args.slice, expected_path)
    state_report = validate_ui_states(state_data, state_manifest.parent, args.slice, current, expected)
    categories = data.get("categories", {})
    problems = []
    if state_report["status"] != "passed":
        problems.extend(f"ui-state: {error}" for error in state_report["errors"])
    total = 0.0
    for name, weight in WEIGHTS.items():
        item = categories.get(name, {})
        score = item.get("score")
        evidence = item.get("evidence", [])
        if type(score) not in (int, float) or not 0 <= score <= 100:
            problems.append(f"{name}: missing or invalid score")
            continue
        if not evidence or not all(value in state_report.get('evidenceHashes', []) for value in evidence):
            problems.append(f"{name}: missing validated evidence content hashes")
        if score < 75:
            problems.append(f"{name}: score below category floor 75")
        total += score * weight / 100
    passed = not problems and total >= args.threshold
    report = {
        "schemaVersion": "2.0",
        "score": round(total, 2),
        "threshold": args.threshold,
        "status": "passed" if passed else "failed",
        "problems": problems,
        "uiStateEvidence": state_report,
        "assurance": "recorded human scores; script validates provenance/coverage, not visual correctness",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
