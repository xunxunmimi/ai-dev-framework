#!/usr/bin/env python3
"""Select exactly one primary framework from lifecycle and task intent."""

from __future__ import annotations

import argparse
import json

from task_router import is_transition_parity_request


def route(lifecycle: str, request: str) -> dict:
    value = request.lower()
    if lifecycle == "ios-maintenance":
        framework, reason = "ios-daily-development-rules", "iOS maintenance lifecycle"
    elif lifecycle == "migration-active":
        framework, reason = "ios-to-harmony-migrator-v2", "active migration lifecycle"
    elif lifecycle == "migration-transition":
        if is_transition_parity_request(request):
            framework, reason = "ios-to-harmony-migrator-v2", "explicit migration parity or evidence residual"
        else:
            framework, reason = "harmonyos-daily-development-rules", "ordinary transition-stage HarmonyOS work"
    elif lifecycle == "harmony-maintenance":
        framework, reason = "harmonyos-daily-development-rules", "HarmonyOS maintenance lifecycle; migration history is read-only"
    else:
        raise ValueError(f"unsupported lifecycle: {lifecycle}")
    return {"framework": framework, "reason": reason, "onePrimaryFramework": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lifecycle", choices=("ios-maintenance", "migration-active", "migration-transition", "harmony-maintenance"), required=True)
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    print(json.dumps(route(args.lifecycle, args.request), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
