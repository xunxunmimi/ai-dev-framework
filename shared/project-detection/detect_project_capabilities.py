#!/usr/bin/env python3
"""Cache cheap, deterministic project capabilities to avoid repeated repository scans."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-routing"))
from read_boundary import ReadBoundary, BoundaryError
from task_runtime import atomic_json


IGNORED = {".git", "DerivedData", ".build", "build", "oh_modules", "node_modules", "Pods", "Carthage", "vendor", ".ai-project", ".migration-workspace-v2"}


def files(root: Path, suffixes: tuple[str, ...], names: set[str]) -> list[Path]:
    """只枚举统一白名单的配置，目录容器由获准配置路径推导，不探测未知兄弟路径。"""
    paths = ReadBoundary(root).files(excludes=IGNORED)
    result = [root / name for name in paths if Path(name).name in names or Path(name).suffix in suffixes]
    for name in paths:
        result.extend(root / parent for parent in Path(name).parents if str(parent) != "." and parent.suffix in suffixes)
    return sorted(set(result))


def relative(root: Path, paths: list[Path]) -> list[str]:
    return [path.relative_to(root).as_posix() for path in paths]


def fingerprint(root: Path, paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        if path.suffix not in {".xcodeproj", ".xcworkspace"}:
            digest.update(ReadBoundary(root).read_bytes(path.relative_to(root).as_posix()))
    return digest.hexdigest()


def detect(root: Path, platform: str) -> dict:
    if platform == "ios":
        configs = files(root, (".xcodeproj", ".xcworkspace", ".xcscheme"), {"Package.swift", "Podfile", "Cartfile", "Package.resolved", "project.pbxproj", "contents.xcworkspacedata", "project.yml", "project.yaml"})
        swift_package = root / "Package.swift"
        candidates = []
        if swift_package in configs:
            candidates = [
                {"id": "swift-build", "argv": ["swift", "build"], "confirmed": False, "purpose": "build"},
                {"id": "swift-test", "argv": ["swift", "test"], "confirmed": False, "purpose": "test"},
            ]
        xcode_containers = [path for path in configs if path.suffix in {".xcodeproj", ".xcworkspace"}]
        return {
            "schemaVersion": "1.1",
            "readCoverage": ReadBoundary(root).coverage(),
            "platform": platform,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "projectFingerprint": fingerprint(root, configs),
            "projectFiles": relative(root, configs),
            "buildCandidates": candidates,
            "needsConfiguration": ["scheme", "configuration", "destination"] if xcode_containers else [],
        }
    configs = files(root, (), {"build-profile.json5", "oh-package.json5", "oh-package-lock.json5", "hvigorfile.ts", "code-linter.json5", "hvigorw"})
    wrapper = root / "hvigorw"
    candidates = []
    if wrapper in configs:
        candidates.append({
            "id": "harmony-build",
            "argv": ["./hvigorw", "assembleHap"],
            "confirmed": False,
            "purpose": "build",
            "reason": "Confirm product, module and buildMode in PROJECT.md before use",
        })
    modules = sorted(path.parent.relative_to(root).as_posix() for path in configs if path.name == "hvigorfile.ts" and path.parent != root)
    return {
        "schemaVersion": "1.1",
            "readCoverage": ReadBoundary(root).coverage(),
        "platform": platform,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "projectFingerprint": fingerprint(root, configs),
        "projectFiles": relative(root, configs),
        "modules": modules,
        "buildCandidates": candidates,
        "needsConfiguration": ["product", "module", "buildMode"] if wrapper in configs else ["build command"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--platform", choices=("ios", "harmonyos"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project = args.project.expanduser().resolve()
    output = args.output.expanduser().resolve()
    boundary = ReadBoundary(project)
    relative_output = args.output.expanduser().absolute().relative_to(project).as_posix()
    boundary.require(relative_output)
    boundary.exists(relative_output)
    data = detect(project, args.platform)
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(output, data)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
