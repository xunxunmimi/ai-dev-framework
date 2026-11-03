#!/usr/bin/env python3
"""Zero-model positive and negative routing checks for the three frameworks."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared/task-routing"))
from framework_router import route  # noqa: E402
from task_router import classify  # noqa: E402


def main() -> int:
    cases = json.loads((ROOT / "evals/cases/framework-routing.json").read_text(encoding="utf-8"))
    assert len(cases) == 14
    failures = []
    for case in cases:
        routed = route(case["lifecycle"], case["prompt"])
        if routed["framework"] != case["expectedFramework"]:
            failures.append(f"{case['id']}: framework={routed['framework']}")
            continue
        mode = "migration" if routed["framework"] == "ios-to-harmony-migrator-v2" else "daily"
        level = classify(case["prompt"], [], mode=mode)["impactLevel"]
        if level != case["expectedLevel"]:
            failures.append(f"{case['id']}: level={level}, expected={case['expectedLevel']}")
    if failures:
        print("\n".join(failures))
        return 1
    print("OK framework routing self-test: 14 lifecycle positive/negative controls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
