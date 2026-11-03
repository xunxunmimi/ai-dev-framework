#!/usr/bin/env python3
"""Zero-model smoke tests for eval definitions, parsing, grading and comparison."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from eval_common import EVAL_ROOT, find_case, grade, parse_trace, validate_cases, write_json


def main() -> int:
    errors = validate_cases()
    assert not errors, errors
    subprocess.run([sys.executable, str(EVAL_ROOT / "scripts" / "routing_self_test.py")], check=True)
    with tempfile.TemporaryDirectory(prefix="eval-self-test-") as temporary:
        root = Path(temporary)
        trace = root / "trace.jsonl"
        events = [
            {"type": "item.started", "item": {"id": "cmd-1", "command": "xcodebuild -scheme App build"}},
            {"type": "item.completed", "item": {"id": "cmd-1", "command": "xcodebuild -scheme App build"}},
            {"type": "turn.completed", "usage": {"input_tokens": 120, "cached_input_tokens": 80, "output_tokens": 30, "total_tokens": 150}},
        ]
        trace.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
        parsed = parse_trace(trace)
        assert parsed["commands"] == ["xcodebuild -scheme App build"]
        assert parsed["tokenUsage"]["totalTokens"] == 150

        case = find_case("ios-quick-d1-nil-guard")
        run = {
            "comparisonContract": {"model": "synthetic", "caseSha256": "fixture", "environment": "test"},
            "suiteIdentity": {"version": "test", "contentSha256": "fixture"},
            "exitCode": 0,
            "durationSeconds": 1.5,
            "result": {"framework": "ios-daily-development-rules", "taskLevel": "D1", "runtimeAcceptance": "user", "changedFiles": ["Sources/ProfileViewController.swift"]},
            "changedFiles": ["Sources/ProfileViewController.swift"],
            "files": ["Sources/ProfileViewController.swift"],
            "contentChecks": [{"id": 1, "path": "Sources/ProfileViewController.swift", "pass": True, "details": "missing=[], forbiddenPresent=[]"}],
            "trace": {"commands": [], "tokenUsage": parsed["tokenUsage"]},
        }
        passing = grade(case, run)
        assert passing["pass"], passing
        run["changedFiles"].append("README.md")
        failing = grade(case, run)
        assert not failing["pass"]
        assert failing["metrics"]["unrelatedChangedFiles"] == 1

        baseline = root / "baseline" / "one"
        candidate = root / "candidate" / "one"
        baseline.mkdir(parents=True)
        candidate.mkdir(parents=True)
        write_json(baseline / "score.json", passing)
        write_json(candidate / "score.json", failing)
        comparison = root / "comparison"
        subprocess.run(
            [sys.executable, str(EVAL_ROOT / "scripts" / "compare_results.py"), "--baseline", str(root / "baseline"), "--candidate", str(root / "candidate"), "--output", str(comparison)],
            check=True,
            capture_output=True,
            text=True,
        )
        compared = json.loads((comparison / "comparison.json").read_text(encoding="utf-8"))
        assert compared["pairedCases"] == 1
        assert compared["regressions"][0]["caseId"] == case["id"]
    print("OK eval self-test: cases, trace usage, scope grading, paired comparison")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
