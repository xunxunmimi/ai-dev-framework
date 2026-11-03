#!/usr/bin/env python3
"""Detect conservative HarmonyOS gate candidates; execution requires confirmation."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def module_names(target: Path) -> list[str]:
    profile = target / "build-profile.json5"
    if not profile.is_file():
        return ["entry"] if (target / "entry").is_dir() else []
    text = profile.read_text(encoding="utf-8", errors="replace")
    names = re.findall(r"['\"]?(?:name|srcPath)['\"]?\s*:\s*['\"]([^'\"]+)['\"]", text)
    modules = []
    for value in names:
        name = Path(value).name
        if (target / name).is_dir() and name not in modules:
            modules.append(name)
    return modules or (["entry"] if (target / "entry").is_dir() else [])


def detect(target: Path, workspace: Path) -> dict:
    modules = module_names(target)
    module = modules[0] if modules else "entry"
    wrapper = target / "hvigorw"
    gates = []
    profiles = {"T0": [], "T1": [], "T2": [], "T3": [], "T4": ["release-manual-evidence"]}
    if wrapper.is_file():
        build_id = "harmony-build-debug"
        gates.append({
            "id": build_id,
            "kind": "command",
            "confirmed": False,
            "detected": True,
            "reason": "Review product/module/buildMode before confirmation",
            "argv": ["./hvigorw", "assembleHap", "--mode", "module", "-p", "product=default", "-p", f"module={module}@default", "-p", "buildMode=debug", "--no-daemon"],
            "cwd": str(target),
            "inputs": ["**/*.ets", "**/*.ts", "**/*.json5", "**/*.json", "**/*.cpp", "**/*.h"],
            "cacheable": True,
            "timeoutSeconds": 900
        })
        profiles["T2"] = [build_id]
        profiles["T3"] = [build_id]
        profiles["T4"] = [build_id, "release-manual-evidence"]
        release = {**gates[0], 'id': 'harmony-build-release', 'argv': [value.replace('buildMode=debug', 'buildMode=release') for value in gates[0]['argv']], 'cacheable': False, 'buildMode': 'release', 'reason': 'Confirm final product/module/environment and Release command; signing remains a separate artifact contract'}
        gates.append(release)
        profiles['T4'] = ['harmony-build-release', 'release-manual-evidence']
    gates.append({
        "id": "release-manual-evidence",
        "kind": "manual",
        "confirmed": False,
        "detected": True,
        "reason": "Configure the project-specific signed build, device, payment/account, and release evidence contract",
        "evidence": str(workspace / "evidence/release/manual-release-evidence.json"),
        "cacheable": False
    })
    return {
        "schemaVersion": "2.1",
        "targetRoot": str(target),
        "evidenceRoot": str(workspace / "evidence/gates"),
        "detectedModules": modules,
        "profilePurposes": {'T4': 'release'},
        "profiles": profiles,
        "gates": gates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--confirm", action="append", default=[])
    args = parser.parse_args()
    target = args.target.expanduser().resolve()
    config_path = args.config.expanduser().resolve()
    workspace = target / ".migration-workspace-v2"
    if args.confirm:
        if not config_path.is_file():
            raise SystemExit("gate config is missing; run --write first")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        by_id = {item.get("id"): item for item in config.get("gates", [])}
        missing = [gate_id for gate_id in args.confirm if gate_id not in by_id]
        if missing:
            raise SystemExit(f"unknown gate ids: {', '.join(missing)}")
        for gate_id in args.confirm:
            by_id[gate_id]["confirmed"] = True
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        config = detect(target, workspace)
        if args.write:
            if config_path.is_file():
                existing = json.loads(config_path.read_text(encoding="utf-8"))
                if existing.get("gates"):
                    print(json.dumps({"status": "kept-existing", "config": str(config_path)}, ensure_ascii=False))
                    return 0
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(config, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
