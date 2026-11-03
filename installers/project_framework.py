#!/usr/bin/env python3
"""Record, audit, and safely update a project's installed framework rules."""

from __future__ import annotations

import argparse
import difflib
import uuid
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# 工具从完整套件加载共享原子写和锁；不会修改系统环境或凭据。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared/task-routing"))
from task_runtime import atomic_json, atomic_text, lock, safe_path, suite_digest
from read_boundary import ReadBoundary



MANIFEST_RELATIVE = Path(".ai-project/framework-installation.json")


MANAGED_MAPPINGS = {
    "ios-daily-development-rules": [
        (".codex-rules/ios/WORKFLOW.md", "frameworks/ios-daily-development-rules/templates/WORKFLOW.md"),
        (".codex-rules/ios/references/verification.md", "frameworks/ios-daily-development-rules/references/verification.md"),
        (".codex-rules/ios/references/code-quality-ios.md", "frameworks/ios-daily-development-rules/references/code-quality-ios.md"),
        (".codex-rules/_shared/task_router.py", "shared/task-routing/task_router.py"),
        (".codex-rules/_shared/framework_router.py", "shared/task-routing/framework_router.py"),
        (".codex-rules/_shared/task_runtime.py", "shared/task-routing/task_runtime.py"),
        (".codex-rules/_shared/read_boundary.py", "shared/task-routing/read_boundary.py"),
        (".codex-rules/_shared/schemas/temporary-external-grant.schema.json", "shared/schemas/temporary-external-grant.schema.json"),
        (".codex-rules/_shared/schemas/metadata-ack.schema.json", "shared/schemas/metadata-ack.schema.json"),
        (".codex-rules/_shared/references/task-control.md", "shared/context-engineering/task-control.md"),
        (".codex-rules/_shared/references/read-boundary.md", "shared/context-engineering/read-boundary.md"),
        (".codex-rules/_shared/references/context-policy.md", "shared/context-engineering/context-policy.md"),
        (".codex-rules/_shared/references/lifecycle.md", "shared/lifecycle/lifecycle.md"),
        (".codex-rules/_shared/references/verification-budget.md", "shared/verification-budget/verification-budget.md"),
        (".codex-rules/_shared/references/code-quality-contract.md", "shared/code-quality/code-quality-contract.md"),
    ],
    "harmonyos-daily-development-rules": [
        (".codex-rules/harmonyos/WORKFLOW.md", "frameworks/harmonyos-daily-development-rules/templates/WORKFLOW.md"),
        (".codex-rules/harmonyos/references/verification.md", "frameworks/harmonyos-daily-development-rules/references/verification.md"),
        (".codex-rules/harmonyos/references/code-quality-harmonyos.md", "frameworks/harmonyos-daily-development-rules/references/code-quality-harmonyos.md"),
        (".codex-rules/_shared/task_router.py", "shared/task-routing/task_router.py"),
        (".codex-rules/_shared/framework_router.py", "shared/task-routing/framework_router.py"),
        (".codex-rules/_shared/task_runtime.py", "shared/task-routing/task_runtime.py"),
        (".codex-rules/_shared/read_boundary.py", "shared/task-routing/read_boundary.py"),
        (".codex-rules/_shared/schemas/temporary-external-grant.schema.json", "shared/schemas/temporary-external-grant.schema.json"),
        (".codex-rules/_shared/schemas/metadata-ack.schema.json", "shared/schemas/metadata-ack.schema.json"),
        (".codex-rules/_shared/references/task-control.md", "shared/context-engineering/task-control.md"),
        (".codex-rules/_shared/references/read-boundary.md", "shared/context-engineering/read-boundary.md"),
        (".codex-rules/_shared/references/context-policy.md", "shared/context-engineering/context-policy.md"),
        (".codex-rules/_shared/references/lifecycle.md", "shared/lifecycle/lifecycle.md"),
        (".codex-rules/_shared/references/verification-budget.md", "shared/verification-budget/verification-budget.md"),
        (".codex-rules/_shared/references/code-quality-contract.md", "shared/code-quality/code-quality-contract.md"),
    ],
    "ios-to-harmony-migrator-v2": [
        (".migration-v2/references/code-quality-contract.md", "shared/code-quality/code-quality-contract.md"),
    ],
}


def sha256(path: Path, project: Path | None = None) -> str:
    """计算文件摘要，供版本和漂移核对；不可读文件抛出异常。"""
    if project is not None:
        return hashlib.sha256(ReadBoundary(project).read_bytes(path.relative_to(project).as_posix())).hexdigest()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_text(project: Path, relative: str) -> str:
    """统一读取项目受管文本；不因安装/升级而绕过业务读取边界。"""
    return ReadBoundary(project).read_text(relative)


def safe_project_path(project: Path, relative: str) -> Path:
    """返回获准且无链接的配置路径；真实缺失可用于新增，未知路径先拒绝。"""
    boundary = ReadBoundary(project)
    name = boundary.require(relative)
    boundary.exists(name)
    return project / name


def suite_version(suite: Path) -> str:
    """读取套件版本，不写入项目。"""
    return (suite / "VERSION").read_text(encoding="utf-8").strip()


def parse_mapping(value: str) -> tuple[str, str]:
    """解析受管相对路径映射；无效输入抛出参数错误。"""
    if "=" not in value:
        raise argparse.ArgumentTypeError("managed mapping must be project/path=suite/path")
    project_path, source_path = value.split("=", 1)
    if not project_path or not source_path or Path(project_path).is_absolute() or Path(source_path).is_absolute():
        raise argparse.ArgumentTypeError("managed mapping paths must be non-empty and relative")
    return project_path, source_path


def record_manifest(
    project: Path,
    suite: Path,
    framework: str,
    lifecycle: str,
    mappings: list[tuple[str, str]],
    persist: bool = True,
) -> dict:
    """记录受管文件和源码摘要；原子写入安装清单，不覆盖业务事实。"""
    project = project.resolve()
    suite = suite.resolve()
    # record 是安装器的内部记录入口，也不能把任意本地漂移静默重置成新基线。
    previous_path = project / MANIFEST_RELATIVE
    if ReadBoundary(project).exists(MANIFEST_RELATIVE.as_posix()):
        previous = ReadBoundary(project).read_json(MANIFEST_RELATIVE.as_posix())
        for item in previous.get("managedFiles", []):
            destination, source = safe_project_path(project, item["path"]), safe_path(suite, item["source"])
            current = sha256(destination, project) if destination.is_file() else None
            if current not in {item["installedSha256"], sha256(source) if source.is_file() else None}:
                raise ValueError("record 不能重置本地漂移，请先审计并人工合并")
    managed = []
    for project_relative, source_relative in mappings:
        destination = safe_project_path(project, project_relative)
        source = (suite / source_relative).resolve()
        if not destination.is_relative_to(project) or not source.is_relative_to(suite):
            raise ValueError("managed mapping escapes project or suite")
        if not destination.is_file() or not source.is_file():
            raise ValueError(f"managed file missing: {project_relative}={source_relative}")
        managed.append({
            "path": project_relative,
            "source": source_relative,
            "installedSha256": sha256(destination, project),
            "sourceSha256": sha256(source),
        })
    manifest = {
        "schemaVersion": "1.0",
        "suiteVersion": suite_version(suite),
        "suiteSha256": suite_digest(suite),
        "framework": framework,
        "lifecycle": lifecycle,
        "installedAt": datetime.now(timezone.utc).isoformat(),
        "suiteRoot": str(suite),
        "managedFiles": managed,
        "contentSha256": hashlib.sha256(json.dumps({item["source"]: item["sourceSha256"] for item in managed}, sort_keys=True).encode()).hexdigest(),
    }
    destination = project / MANIFEST_RELATIVE
    if persist:
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(destination, manifest)
    return manifest


def read_manifest(project: Path) -> dict:
    """读取已安装规则清单；缺失时要求先建立可审计基线。"""
    path = project / MANIFEST_RELATIVE
    if not path.is_file():
        raise ValueError(f"framework installation manifest is missing: {path}")
    return ReadBoundary(project).read_json(MANIFEST_RELATIVE.as_posix())


def audit_project(project: Path, suite: Path) -> dict:
    """只读比较安装、源码和项目规则，返回漂移、更新状态与差异预览。"""
    project = project.resolve()
    suite = suite.resolve()
    manifest = read_manifest(project)
    files = []
    for item in manifest.get("managedFiles", []):
        destination = safe_project_path(project, item["path"])
        source = (suite / item["source"]).resolve()
        if not destination.is_relative_to(project) or not source.is_relative_to(suite):
            files.append({**item, "localState": "invalid-path", "updateAvailable": False})
            continue
        local_digest = sha256(destination, project) if destination.is_file() else None
        source_digest = sha256(source) if source.is_file() else None
        local_state = "missing" if local_digest is None else (
            "unchanged" if local_digest == item.get("installedSha256") else "modified"
        )
        files.append({
            **item,
            "localState": local_state,
            "currentSha256": local_digest,
            "currentSourceSha256": source_digest,
            "updateAvailable": source_digest is not None and (
                source_digest != item.get("sourceSha256") or item.get("installedSha256") != item.get("sourceSha256")
            ),
        })
    known = {item["path"] for item in files}
    for relative, source_relative in MANAGED_MAPPINGS.get(manifest.get("framework"), []):
        if relative not in known:
            destination, source = safe_project_path(project, relative), safe_path(suite, source_relative)
            files.append({"path": relative, "source": source_relative, "localState": "unmanaged-existing" if destination.exists() else "new", "installedSha256": None, "sourceSha256": None, "currentSha256": sha256(destination, project) if destination.is_file() else None, "currentSourceSha256": sha256(source), "updateAvailable": True})
    for item in files:
        destination, source = safe_project_path(project, item["path"]), safe_path(suite, item["source"])
        if source.is_file():
            before = project_text(project, destination.relative_to(project).as_posix()).splitlines(True) if destination.is_file() else []
            item["diff"] = "".join(difflib.unified_diff(before, source.read_text().splitlines(True), fromfile=item["path"], tofile=item["source"]))
    current_version = suite_version(suite)
    local_drift = [item["path"] for item in files if item["localState"] not in {"unchanged", "new"}]
    return {
        "schemaVersion": "1.0",
        "project": str(project),
        "framework": manifest.get("framework"),
        "lifecycle": manifest.get("lifecycle"),
        "installedVersion": manifest.get("suiteVersion"),
        "currentSuiteVersion": current_version,
        "updateAvailable": manifest.get("suiteVersion") != current_version or manifest.get("suiteSha256") != suite_digest(suite) or any(item["updateAvailable"] for item in files),
        "safeToUpdate": not local_drift,
        "suiteContentChanged": manifest.get("suiteSha256") != suite_digest(suite),
        "localDrift": local_drift,
        "managedFiles": files,
    }


def upgrade(project: Path, suite: Path) -> dict:
    """锁定并重审计后升级受管文件；保存原文及计划摘要，异常/断线后可显式恢复。"""
    with lock(project / ".ai-project/framework-update.lock"):
        audit = audit_project(project, suite)
        if not audit["safeToUpdate"]:
            raise ValueError("Refusing to overwrite locally modified managed files")
        # 唯一事务目录防止连续升级覆盖前一次备份；只存受管文本和清单，不拷贝业务工程。
        transaction = project / ".ai-project/upgrades" / uuid.uuid4().hex
        paths = [item["path"] for item in audit["managedFiles"]] + [str(MANIFEST_RELATIVE), ".ai-project/lifecycle.json"]
        before = {name: project_text(project, name) if safe_project_path(project, name).is_file() else None for name in paths}
        planned = {item["path"]: safe_path(suite, item["source"]).read_text() for item in audit["managedFiles"]}
        lifecycle = json.loads(before[".ai-project/lifecycle.json"]) if before[".ai-project/lifecycle.json"] else {}
        lifecycle["frameworkVersion"] = suite_version(suite)
        # 在实际修改前计算所有目标内容，断线后也能识别计划内写入并安全恢复。
        planned[".ai-project/lifecycle.json"] = json.dumps(lifecycle, ensure_ascii=False, indent=2) + "\n"
        # 清单哈希应描述即将安装的内容，不能摘要尚未写入的旧文件。
        future_managed = [{"path": item["path"], "source": item["source"], "installedSha256": sha256(safe_path(suite, item["source"])), "sourceSha256": sha256(safe_path(suite, item["source"]))} for item in audit["managedFiles"]]
        future_manifest = {"schemaVersion": "1.0", "suiteVersion": suite_version(suite), "framework": audit["framework"], "lifecycle": audit["lifecycle"], "installedAt": datetime.now(timezone.utc).isoformat(), "suiteRoot": str(suite), "managedFiles": future_managed, "suiteSha256": suite_digest(suite)}
        planned[str(MANIFEST_RELATIVE)] = json.dumps(future_manifest, ensure_ascii=False, indent=2) + "\n"
        record = {"projectRoot": str(project), "status": "applying", "before": before, "planned": planned, "createdAt": datetime.now(timezone.utc).isoformat()}
        atomic_json(transaction / "transaction.json", record)
        for name, text in planned.items():
            atomic_text(safe_project_path(project, name), text)
        record["after"] = {name: project_text(project, name) for name in paths}
        record["status"] = "complete"
        atomic_json(transaction / "transaction.json", record)
        return {"status": "updated", "transaction": str(transaction), "version": suite_version(suite)}


def restore(project: Path, transaction: Path) -> dict:
    """恢复指定升级事务；恢复前逐项核对当前内容，遇到后续本地修改拒绝覆盖。"""
    if not transaction.resolve().is_relative_to((project / ".ai-project/upgrades").resolve()):
        raise ValueError("恢复记录必须属于当前项目 upgrades 目录")
    with lock(project / ".ai-project/framework-update.lock"):
        record = ReadBoundary(project).read_json((transaction / "transaction.json").relative_to(project).as_posix())
        if record["projectRoot"] != str(project):
            raise ValueError("升级事务属于其他项目")
        for name, before in record["before"].items():
            path = safe_project_path(project, name)
            current = project_text(project, name) if ReadBoundary(project).exists(name) else None
            allowed = [before]
            if name in record.get("after", {}):
                allowed.append(record["after"][name])
            if name in record["planned"]:
                allowed.append(record["planned"][name])
            if current not in allowed:
                raise ValueError(f"恢复会覆盖后续漂移，拒绝: {name}")
        for name, before in record["before"].items():
            path = safe_project_path(project, name)
            if before is None:
                path.unlink(missing_ok=True)
            else:
                atomic_text(path, before)
        record["status"] = "restored"
        atomic_json(transaction / "transaction.json", record)
        return {"status": "restored", "transaction": str(transaction)}


def main() -> int:
    """分派审计、预览、升级或恢复；实际更新有独立锁和备份。"""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record")
    record.add_argument("--project", type=Path, required=True)
    record.add_argument("--suite", type=Path, required=True)
    record.add_argument("--framework", required=True)
    record.add_argument("--lifecycle", required=True)
    record.add_argument("--managed", action="append", default=[], type=parse_mapping)
    audit = sub.add_parser("audit")
    audit.add_argument("project", type=Path)
    audit.add_argument("--suite", type=Path, required=True)
    update = sub.add_parser("update")
    update.add_argument("project", type=Path)
    update.add_argument("--suite", type=Path, required=True)
    update.add_argument("--dry-run", action="store_true")
    adopt = sub.add_parser("adopt")
    adopt.add_argument("project", type=Path)
    adopt.add_argument("--suite", type=Path, required=True)
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("project", type=Path)
    restore_parser.add_argument("--suite", type=Path, required=True)
    restore_parser.add_argument("--transaction", type=Path, required=True)
    args = parser.parse_args()
    project = args.project.expanduser().resolve()
    suite = args.suite.expanduser().resolve()
    if args.command == "restore":
        print(json.dumps(restore(project, args.transaction.resolve()), ensure_ascii=False, indent=2))
        return 0
    if args.command == "record":
        result = record_manifest(project, suite, args.framework, args.lifecycle, args.managed)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "adopt":
        if (project / MANIFEST_RELATIVE).exists():
            raise ValueError("项目已有安装清单，adopt 不能重置基线；请 audit/update")
        lifecycle_path = project / ".ai-project/lifecycle.json"
        if not lifecycle_path.is_file():
            raise ValueError("cannot adopt project without .ai-project/lifecycle.json")
        lifecycle = ReadBoundary(project).read_json(".ai-project/lifecycle.json")
        framework = lifecycle.get("defaultFramework")
        if lifecycle.get("state") == "migration-active" or (
            (project / ".migration-v2/project.json").is_file() and framework == "ios-to-harmony-migrator-v2"
        ):
            framework = "ios-to-harmony-migrator-v2"
        mappings = [item for item in MANAGED_MAPPINGS.get(framework, []) if (project / item[0]).is_file()]
        if not mappings:
            raise ValueError(f"no recognizable managed files for {framework}")
        result = record_manifest(project, suite, framework, lifecycle.get("state", "unknown"), mappings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    result = audit_project(project, suite)
    if args.command == "audit":
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["safeToUpdate"] else 2
    if not result["safeToUpdate"]:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("Refusing to overwrite locally modified managed files.", file=sys.stderr)
        return 3
    if args.dry_run:
        print(json.dumps({"audit": result, "applyMode": "managed-files-only", "recovery": "python3 installers/project_framework.py restore <project> --suite <suite> --transaction <upgrade-directory>"}, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(upgrade(project, suite), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
