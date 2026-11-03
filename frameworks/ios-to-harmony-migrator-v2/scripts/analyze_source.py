#!/usr/bin/env python3
"""Create a content-addressed iOS source index and a compact discovery baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

for parent in Path(__file__).resolve().parents:
    shared = parent / "shared/task-routing"
    if (shared / "read_boundary.py").is_file():
        sys.path.insert(0, str(shared))
        break
from read_boundary import require_complete, SNAPSHOT_EXCLUDES


IGNORED_DIRS = {
    ".git", ".build", ".swiftpm", "Pods", "Carthage", "DerivedData",
    "build", "node_modules", "vendor",
}
SENSITIVE_NAMES = {".env", "GoogleService-Info.plist"}
SENSITIVE_SUFFIXES = {".cer", ".key", ".mobileprovision", ".p12", ".pem"}
SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".h", ".hpp", ".m", ".mm", ".swift"}
RESOURCE_SUFFIXES = {
    ".aac", ".aiff", ".gif", ".heic", ".jpeg", ".jpg", ".json", ".lottie",
    ".mp3", ".mp4", ".otf", ".pdf", ".png", ".svg", ".ttf", ".wav", ".webp",
}
DEPENDENCY_NAMES = {
    "Cartfile", "Cartfile.resolved", "Package.resolved", "Package.swift", "Podfile", "Podfile.lock",
}
CONFIG_SUFFIXES = {".entitlements", ".plist", ".privacy", ".xcconfig"}
LOCALIZATION_SUFFIXES = {".strings", ".stringsdict", ".xcstrings"}
REQUIRED_AREAS = (
    "projectStructure",
    "architectureAndModules",
    "screensAndNavigation",
    "interactionsAndStates",
    "featuresAndBusinessFlows",
    "dependenciesAndLicenses",
    "dataStorageAndMigration",
    "networkAndBackendContracts",
    "permissionsAndSystemCapabilities",
    "resourcesLocalizationAccessibility",
    "testsBuildSigningRelease",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_revision(source: Path) -> dict:
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(source), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    head = git("rev-parse", "HEAD")
    if head.returncode != 0:
        return {"kind": "content-index", "revision": "not-a-git-repository", "dirty": None}
    dirty = git("status", "--porcelain")
    return {
        "kind": "git",
        "revision": head.stdout.strip(),
        "dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
    }


def category(relative: Path) -> str:
    lowered_parts = {part.lower() for part in relative.parts}
    name = relative.name
    suffix = relative.suffix.lower()
    if name in DEPENDENCY_NAMES or any(part.endswith((".framework", ".xcframework")) for part in relative.parts):
        return "dependencies"
    if "test" in " ".join(lowered_parts) or "tests" in lowered_parts or "uitests" in lowered_parts:
        return "tests"
    if suffix in {".storyboard", ".xib"}:
        return "ui"
    if suffix in LOCALIZATION_SUFFIXES or any(part.endswith(".lproj") for part in relative.parts):
        return "localization"
    if ".xcassets" in " ".join(lowered_parts) or suffix in RESOURCE_SUFFIXES:
        return "resources"
    if suffix in SOURCE_SUFFIXES:
        return "source"
    if suffix in CONFIG_SUFFIXES or name == "project.pbxproj" or suffix == ".pbxproj":
        return "configuration"
    return "other"


def build_source_index(source: Path) -> dict:
    """仅索引完整授权且通过统一防护的源文件；私密路径不打开或摘要。"""
    boundary = require_complete(source)
    files = []
    skipped_sensitive = 0
    project_bundles = []
    for name in boundary.files(excludes=SNAPSHOT_EXCLUDES | IGNORED_DIRS):
        relative = Path(name)
        if relative.name in SENSITIVE_NAMES or relative.suffix.lower() in SENSITIVE_SUFFIXES:
            skipped_sensitive += 1
            continue
        for position, part in enumerate(relative.parts[:-1]):
            if Path(part).suffix in {".xcodeproj", ".xcworkspace"}:
                bundle = Path(*relative.parts[:position + 1]).as_posix()
                if bundle not in project_bundles:
                    project_bundles.append(bundle)
        content = boundary.read_bytes(name)
        files.append({"path": name, "category": category(relative), "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    files.sort(key=lambda item: item["path"])

    category_counts: dict[str, int] = {}
    for item in files:
        key = item["category"]
        category_counts[key] = category_counts.get(key, 0) + 1

    project_bundles.sort()
    fingerprint_payload = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {
        "schemaVersion": "2.2",
        "sourceRoot": str(source),
        "revision": source_revision(source),
        "sourceFingerprint": hashlib.sha256(fingerprint_payload).hexdigest(),
        "totalFiles": len(files),
        "sensitiveFilesSkipped": skipped_sensitive,
        "readCoverage": boundary.coverage(),
        "privateExclusionCount": "not-enumerated; excluded before opening",
        "categoryCounts": category_counts,
        "projectBundles": project_bundles,
        "files": files,
    }


def empty_area() -> dict:
    return {
        "status": "pending",
        "summary": "",
        "notApplicableReason": "",
        "evidenceRefs": [],
        "coverage": {"identified": 0, "mapped": 0, "excluded": 0, "unknown": 0},
        "items": [],
    }


def new_baseline(source: Path, index: dict, index_sha256: str) -> dict:
    return {
        "schemaVersion": "2.2",
        "status": "discovery-in-progress",
        "source": {
            "root": str(source),
            "revision": index["revision"],
            "indexPath": "ir/source-index.json",
            "currentSourceIndexSha256": index_sha256,
            "analysedSourceIndexSha256": None,
        },
        "analysisCoverage": {
            "sourceFilesIndexed": index["totalFiles"],
            "sourceFilesAccountedFor": 0,
        },
        "areas": {name: empty_area() for name in REQUIRED_AREAS},
        "decisions": [],
        "notes": "Fill summaries and compact traceable items; keep raw file inventory in source-index.json.",
    }


def prepare_discovery(source: Path, workspace: Path) -> dict:
    ir = workspace / "ir"
    ir.mkdir(parents=True, exist_ok=True)
    index_path = ir / "source-index.json"
    baseline_path = ir / "discovery-baseline.json"
    index = build_source_index(source)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    index_sha256 = file_sha256(index_path)

    if baseline_path.is_file():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        previous_current = baseline.get("source", {}).get("currentSourceIndexSha256")
        baseline.setdefault("source", {})
        baseline["source"].update({
            "root": str(source),
            "revision": index["revision"],
            "indexPath": "ir/source-index.json",
            "currentSourceIndexSha256": index_sha256,
        })
        baseline.setdefault("analysisCoverage", {})["sourceFilesIndexed"] = index["totalFiles"]
        if previous_current != index_sha256:
            baseline["status"] = "discovery-needs-reconciliation"
    else:
        baseline = new_baseline(source, index, index_sha256)

    baseline_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "sourceIndex": str(index_path),
        "sourceIndexSha256": index_sha256,
        "discoveryBaseline": str(baseline_path),
        "sourceFilesIndexed": index["totalFiles"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("workspace", type=Path)
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    workspace = args.workspace.expanduser().resolve()
    if not source.is_dir():
        raise SystemExit(f"iOS source does not exist: {source}")
    print(json.dumps(prepare_discovery(source, workspace), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
