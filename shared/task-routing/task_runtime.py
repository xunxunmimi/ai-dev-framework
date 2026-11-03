#!/usr/bin/env python3
"""任务控制工具：核验身份、隔离状态、保留交接，并把验证证据绑定真实文件。"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import tempfile
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path

from read_boundary import ReadBoundary, BoundaryError, SNAPSHOT_EXCLUDES, require_complete, task_read_scope, bootstrap_bytes, secure_fd

# 仅跳过生成缓存和任务自身状态；业务源码、规则和第三方目录仍参与差异检查。
IGNORED_DIRS = {".git", ".ai-project", ".migration-workspace-v2", "DerivedData", "build", "node_modules", "__pycache__", ".aws", ".ssh", ".gnupg"}
# 不读取凭据内容；这类文件始终不应成为任务允许编辑范围。
SECRET_SUFFIXES = {".p12", ".pem", ".key", ".mobileprovision", ".cer"}
# 默认禁止修改的依赖目录；项目可追加公共库路径，不能削弱这些默认值。
PROTECTED = (".git/**", ".aws/**", ".ssh/**", ".gnupg/**", "Pods/**", "**/Pods/**", "Carthage/**", "**/Carthage/**", "vendor/**", "**/vendor/**", "oh_modules/**", "**/oh_modules/**", ".build/**", "**/*.xcframework/**", "**/*.framework/**")
# 完成维度互相独立；提交与推送不能由编译成功推导。
DIMENSIONS = ("configuration", "codeModified", "compiled", "automatedTests", "realDevice", "submitted", "pushed")

# 此候选仅承接已核实的 iOS 版本映射，不能由事务自声明新受管路径。
IOS_TRUSTED_MAPPING = {
    '.codex-rules/ios/WORKFLOW.md': 'frameworks/ios-daily-development-rules/templates/WORKFLOW.md',
    '.codex-rules/ios/references/verification.md': 'frameworks/ios-daily-development-rules/references/verification.md',
    '.codex-rules/ios/references/code-quality-ios.md': 'frameworks/ios-daily-development-rules/references/code-quality-ios.md',
    '.codex-rules/_shared/task_router.py': 'shared/task-routing/task_router.py',
    '.codex-rules/_shared/task_runtime.py': 'shared/task-routing/task_runtime.py',
    '.codex-rules/_shared/framework_router.py': 'shared/task-routing/framework_router.py',
    '.codex-rules/_shared/read_boundary.py': 'shared/task-routing/read_boundary.py',
    '.codex-rules/_shared/references/task-control.md': 'shared/context-engineering/task-control.md',
    '.codex-rules/_shared/references/read-boundary.md': 'shared/context-engineering/read-boundary.md',
    '.codex-rules/_shared/references/context-policy.md': 'shared/context-engineering/context-policy.md',
    '.codex-rules/_shared/references/lifecycle.md': 'shared/lifecycle/lifecycle.md',
    '.codex-rules/_shared/references/verification-budget.md': 'shared/verification-budget/verification-budget.md',
    '.codex-rules/_shared/references/code-quality-contract.md': 'shared/code-quality/code-quality-contract.md',
    '.codex-rules/_shared/schemas/temporary-external-grant.schema.json': 'shared/schemas/temporary-external-grant.schema.json',
    '.codex-rules/_shared/schemas/metadata-ack.schema.json': 'shared/schemas/metadata-ack.schema.json',
}
IOS_252_MAPPING = {k: v for k, v in IOS_TRUSTED_MAPPING.items() if '/schemas/' not in k}


def trusted_ios_manifest(manifest: dict) -> None:
    """仅接受此候选实际核验过的 iOS 版本和精确路径到来源映射。"""
    version = manifest.get('suiteVersion')
    expected = IOS_252_MAPPING if version == '2.5.2' else IOS_TRUSTED_MAPPING if version in {'2.6.1', '2.6.2-ruler-task-handoff'} else None
    allowed_keys = {'schemaVersion', 'suiteVersion', 'framework', 'lifecycle', 'installedAt', 'suiteRoot', 'managedFiles', 'suiteSha256', 'contentSha256'}
    required_keys = allowed_keys - {'contentSha256'}
    if expected is None or manifest.get('schemaVersion') != '1.0' or manifest.get('framework') != 'ios-daily-development-rules' or manifest.get('lifecycle') != 'ios-maintenance' or set(manifest) - allowed_keys or not required_keys.issubset(manifest):
        raise ValueError('未核验的框架版本或安装清单字段')
    if not isinstance(manifest['suiteRoot'], str) or not Path(manifest['suiteRoot']).is_absolute() or not re.fullmatch(r'[0-9a-f]{64}', manifest['suiteSha256'] or ''):
        raise ValueError('来源路径或套件摘要无效')
    entries = manifest.get('managedFiles')
    if not isinstance(entries, list) or len(entries) != len(expected): raise ValueError('受管映射数量不符')
    observed = {}
    for entry in entries:
        if set(entry) != {'path', 'source', 'installedSha256', 'sourceSha256'} or entry['path'] in observed:
            raise ValueError('受管映射字段或路径无效')
        if entry['installedSha256'] != entry['sourceSha256'] or not re.fullmatch(r'[0-9a-f]{64}', entry['installedSha256']):
            raise ValueError('受管来源摘要不符')
        observed[entry['path']] = entry['source']
    if observed != expected: raise ValueError('受管映射与已核验版本不符')


def task_scoped(function):
    """每次调用按显式任务 ID 装载临时范围，结束后恢复上下文，避免跨线程/任务污染。"""
    @wraps(function)
    def scoped(root, task=None, *args, **kwargs):
        """保留原接口；check_scope 的第二参数是含 taskId 的状态。"""
        if task is None:
            task = kwargs.pop('task_id', None) if 'task_id' in kwargs else kwargs.pop('state', None)
        task_id = task['taskId'] if isinstance(task, dict) else task
        with task_read_scope(root, task_id, kwargs.get('read_policy')):
            return function(root, task, *args, **kwargs)
    return scoped


def now() -> str:
    """返回带 UTC 时区的当前时间；无副作用，供状态与报告统一计时。"""
    return datetime.now(timezone.utc).isoformat()


def digest(value: object) -> str:
    """返回结构化值的稳定 SHA256；不使用文件修改时间代替内容身份。"""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_json(path: Path, root: Path | None = None) -> dict:
    """读取 JSON 对象；文件缺失、格式错误由调用方处理，不生成默认成功状态。"""
    # 项目控制状态也必须遵守白名单，不能经 state/manifest 链接绕出边界。
    if root is None:
        for marker in (".ai-project", ".codex-rules", ".migration-v2", ".migration-workspace-v2"):
            if marker in path.parts:
                root = Path(*path.parts[:path.parts.index(marker)])
                break
    if root is not None:
        return ReadBoundary(root).read_json(path.relative_to(root).as_posix())
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_text(path: Path, text: str) -> None:
    """在同目录写临时文件并原子替换；写入失败保留旧文件，临时文件最终清理。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    # 同文件系统的替换防止断线留下半份 JSON；并发一致性另由 lock 保证。
    fd, temporary = tempfile.mkstemp(prefix=".ai-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path: Path, value: object) -> None:
    """把对象原子写为可读 JSON；仅对给定路径产生写入副作用。"""
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


@contextlib.contextmanager
def lock(path: Path):
    """取得非等待文件锁；冲突抛错，进程退出自动释放，不遗留需手删的占用标记。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("状态正在被另一进程修改，请稍后重试") from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def project_lock(root: Path, relative: str):
    """项目锁只允许在授权控制路径创建；既有锁若是链接或特殊文件则拒绝。"""
    ReadBoundary(root).exists(relative)
    return lock(root / relative)


def git(root: Path, *args: str) -> str:
    """执行只读 Git 查询并返回输出；非仓库返回空串，不读取远程 URL 或凭据。"""
    if ReadBoundary(root).policy["mode"] != "complete":
        return ""  # Git 自身可能打开工作树内容；受限模式不调用外部探测。
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    return result.stdout.rstrip("\n") if result.returncode == 0 else ""


def project_relative_paths(root: Path, repository_paths: list[str]) -> list[str]:
    """把 porcelain 的仓库根相对路径转换成项目相对路径；过滤同仓库其他项目的改动。"""
    top = git(root, "rev-parse", "--show-toplevel")
    repository = Path(top).resolve() if top else root.resolve()
    result = []
    for name in repository_paths:
        absolute = (repository / name).resolve()
        if absolute.is_relative_to(root.resolve()):
            result.append(absolute.relative_to(root.resolve()).as_posix())
    return sorted(set(result))


def safe_path(root: Path, relative: str) -> Path:
    """把项目相对路径解析到项目内；绝对路径、父级逃逸和指向外部的链接均拒绝。"""
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"任务文件必须是项目内相对路径: {relative}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"路径越过项目边界: {relative}")
    return resolved


def file_hash(path: Path, root: Path | None = None) -> str:
    """流式计算文件摘要；链接只摘要链接目标字符串，不读取项目外内容。"""
    if root is not None:
        return hashlib.sha256(ReadBoundary(root).read_bytes(path.relative_to(root).as_posix())).hexdigest()
    if path.is_symlink():
        raise BoundaryError("不摘要符号链接目标或内容")
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def secret(path: Path) -> bool:
    """识别不应读取或编辑的凭据文件名；这是保守筛选，不保证发现所有秘密。"""
    return path.name == ".env" or path.name.startswith(".env.") or path.suffix.lower() in SECRET_SUFFIXES


def capture_snapshot(root: Path) -> dict:
    """只读已明确授权的业务与规则，返回内容和覆盖范围；读取错误抛阻塞而不静默跳过。"""
    boundary = ReadBoundary(root)
    result = {}  # 未列入白名单的内容不遍历、不打开，不对其作未改变的承诺。
    for relative in boundary.files(excludes=SNAPSHOT_EXCLUDES):
        if relative in {".migration-v2/CURRENT.md", ".migration-v2/task-context.json", ".migration-v2/task-baseline.json"} or Path(relative).name == ".DS_Store":
            continue
        data = boundary.read_bytes(relative)
        if relative == ".migration-v2/project.json":
            value = json.loads(data)
            value.pop("phase", None)
            result[relative] = digest(value)
        else:
            result[relative] = hashlib.sha256(data).hexdigest()
    return {"paths": result, "coverage": boundary.coverage()}


def tree_snapshot(root: Path) -> dict:
    """兼容路径摘要接口；调用方不能将部分白名单摘要解释为全项目验证。"""
    return capture_snapshot(root)["paths"]


def task_state_paths(root: Path) -> list[Path]:
    """只在获准的任务状态子树枚举；链接与未知路径在读取前拒绝。"""
    return [root / name for name in ReadBoundary(root).files(subtree=".ai-project/tasks")
            if len(Path(name).parts) == 4 and name.endswith("/state.json")]


def observe_identity(root: Path, platform: str, selection: dict | None = None) -> dict:
    """只读探测项目身份；selection 是明确确认的目标选择，未知或冲突不会被猜成已确认。"""
    root = root.resolve()
    selection = selection or {}
    boundary = ReadBoundary(root)
    identity_files = boundary.project_policy.get('identityFiles')
    if identity_files is not None:
        if not isinstance(identity_files, list) or not identity_files:
            raise BoundaryError('声明的身份配置路径必须为非空列表')
        # 身份核对只读明确配置，不为了辨认 Bundle/Scheme 先摘要整个业务树。
        files = {name: hashlib.sha256(boundary.read_bytes(name)).hexdigest() for name in identity_files}
    else:
        files = capture_snapshot(root)["paths"]
    ios = [name for name in files if name.endswith("project.pbxproj") or name == "Package.swift"]
    harmony = [name for name in files if name == "build-profile.json5"]
    detected = "mixed" if ios and harmony else "ios" if ios else "harmonyos" if harmony else "unknown"
    if platform not in {"ios", "harmonyos"} or detected not in {platform, "mixed"}:
        raise ValueError(f"平台冲突或未识别: requested={platform}, detected={detected}")
    # 不保存远程 URL；仓库公共目录和根路径共同区分项目与 worktree。
    observe_git = boundary.project_policy.get('identityGitMode') != 'unverified'
    top = git(root, "rev-parse", "--show-toplevel") if observe_git else ''
    common = git(root, "rev-parse", "--git-common-dir") if observe_git else ''
    branch = (git(root, "symbolic-ref", "--short", "HEAD") or ("detached:" + git(root, "rev-parse", "HEAD") if top else "not-applicable")) if observe_git else 'unverified-read-scope'
    configs = [name for name in files if name.endswith(("project.pbxproj", ".xcscheme", "build-profile.json5", "AppScope/app.json5")) or name in {"project.yml", "project.yaml", "Package.swift"}]
    text = "\n".join(boundary.read_text(name) for name in configs)
    candidates = {
        "bundleId": sorted(set(re.findall(r'(?:PRODUCT_BUNDLE_IDENTIFIER\s*=\s*|["\']?bundleName["\']?\s*:\s*["\'])([A-Za-z0-9_.-]+)', text))),
        "scheme": sorted({Path(name).stem for name in configs if name.endswith(".xcscheme")}),
        "product": sorted(set(re.findall(r'["\']?name["\']?\s*:\s*["\']([^"\']+)', boundary.read_text("build-profile.json5")) if harmony else [])),
        "module": sorted({Path(name).parts[0] for name in files if name.endswith("module.json5")}),
        "buildMode": ["debug", "release"] if harmony else [],
    }
    # App 可以同时依赖根目录 Package；存在 Xcode App 配置时仍必须核验 Bundle/Scheme。
    has_xcode = any(name.endswith("project.pbxproj") for name in files)
    required = ("bundleId", "scheme") if platform == "ios" and (has_xcode or "Package.swift" not in files) else ("bundleId", "product", "module", "buildMode") if platform == "harmonyos" else ()
    fields = {}  # 每个字段保存状态，避免空串被误当成“不适用”。
    for name in candidates:
        if name not in required:
            fields[name] = {"status": "not-applicable", "value": None}
            continue
        value = selection.get(name)
        choices = candidates[name]
        if value and choices and value not in choices:
            raise ValueError(f"身份字段冲突: {name}={value}, candidates={choices}")
        if value or len(choices) == 1:
            fields[name] = {"status": "confirmed" if value else "observed", "value": value or choices[0]}
        else:
            fields[name] = {"status": "needs-confirmation", "value": None, "candidates": choices}
    return {"projectRoot": str(root), "repositoryRoot": str(Path(top).resolve()) if top else "unverified-read-scope" if not observe_git or boundary.policy["mode"] == "partial" else "not-applicable", "repositoryId": digest(str((root / common).resolve())) if common else "not-applicable", "branch": branch, "platform": platform, "detectedPlatform": detected, "fields": fields, "configSha256": digest({name: files[name] for name in configs}), "readCoverage": {"definition": "identity-configurations-only", "projectPolicySha256": ReadBoundary(root).coverage()["projectPolicySha256"], "paths": sorted(configs), "businessTree": "not-assessed"}}


def suite_digest(suite: Path) -> str:
    """摘要套件活动源码、Skill 和窄能力；排除缓存/发布产物，发现同版本不同内容。"""
    files = {}
    for folder in ("frameworks", "shared", "installers", "capabilities"):
        for path in (suite / folder).rglob("*"):
            if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts and path.name != ".DS_Store":
                files[path.relative_to(suite).as_posix()] = file_hash(path)
    return digest(files)


def rules_identity(root: Path) -> dict:
    """核对项目受管规则与套件版本；本地漂移/旧版暂停，未接入明确标记不适用。"""
    manifest_path = root / ".ai-project/framework-installation.json"
    boundary = ReadBoundary(root)
    if not boundary.exists(".ai-project/framework-installation.json"):
        return {"status": "not-installed"}
    manifest = read_json(manifest_path)
    suite = Path(manifest["suiteRoot"])
    if not (suite / "VERSION").is_file():
        raise ValueError("框架源码不可用，无法核对规则版本")
    if (suite / "VERSION").read_text().strip() != manifest["suiteVersion"]:
        raise ValueError("套件与项目规则版本不同，先 audit/dry-run，不能自动覆盖")
    if manifest.get("suiteSha256") and manifest["suiteSha256"] != suite_digest(suite):
        raise ValueError("同版本套件源码/Skill 摘要变化，先审计再更新规则")
    for item in manifest["managedFiles"]:
        path, source = root / item["path"], safe_path(suite, item["source"])
        if not boundary.exists(item["path"]) or file_hash(path, root) != item["installedSha256"]:
            raise ValueError(f"项目规则发生本地漂移: {item['path']}")
        if not source.is_file() or file_hash(source) != item["sourceSha256"]:
            raise ValueError(f"同版本框架内容发生漂移: {item['source']}")
    return {"version": manifest["suiteVersion"], "managedSha256": digest(manifest["managedFiles"])}


def confirmed_identity(root: Path, validated_rules: dict | None = None) -> dict:
    """读取身份基线并重探测；根、仓库、分支、平台或配置变化均暂停，等待明确重新确认。"""
    if validated_rules is None:
        rules_identity(root)
    expected = read_json(root / ".ai-project/identity.json")
    selection = {name: field["value"] for name, field in expected["fields"].items() if field["status"] == "confirmed"}
    current = observe_identity(root, expected["platform"], selection)
    if current != expected or any(field["status"] == "needs-confirmation" for field in current["fields"].values()):
        raise ValueError("项目身份或配置已变化，请只读核对后重新确认 identity，禁止自动接受冲突")
    return current


def task_dir(root: Path, task_id: str) -> Path:
    """返回任务专属目录；任务标识仅接受安全字符，不能借 ID 写出状态目录。"""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", task_id):
        raise ValueError("task-id 必须是 1–80 位字母、数字、点、下划线或短横线")
    boundary = ReadBoundary(root)
    boundary.exists(f".ai-project/tasks/{task_id}/state.json")
    return safe_path(root, f".ai-project/tasks/{task_id}")


def match(path: str, patterns: list | tuple) -> bool:
    """匹配项目相对路径与 glob；目录根也视作所属范围，防止目录本身重命名绕过检查。"""
    return any(fnmatch.fnmatchcase(path, pattern) or path == pattern.removesuffix('/**') for pattern in patterns)


@task_scoped
def begin(root: Path, task_id: str, files: list[str], request: str, owner: str = "user-session", protected: list | None = None, read_policy: dict | None = None) -> dict:
    """开始新任务并保留不可变基线。

    root 为项目根，task_id 为唯一任务 ID，files 为允许编辑的相对文件，request 为需求；
    owner 记录任务归属，protected 只能追加禁改 glob。返回机器状态，写 baseline/state/handoff；
    身份不符、已有用户修改、范围重叠或重复 ID 抛错，不覆盖旧任务。
    """
    # 统一 Mac 目录别名，避免把本任务误认成另一个任务或项目。
    root = root.resolve()
    identity = confirmed_identity(root)
    directory = task_dir(root, task_id)
    files = sorted(set(files))
    if not files:
        raise ValueError("持久任务必须声明 --file 编辑范围；范围未知时先只读调查")
    policy_path = root / ".ai-project/protected-paths.json"
    boundary = ReadBoundary(root)
    project_protected = read_json(policy_path).get("patterns", []) if boundary.exists(".ai-project/protected-paths.json") else []
    blocked = list(PROTECTED) + project_protected + list(protected or [])
    for name in files:
        safe_path(root, name)
        boundary.require(name)
        if match(name, blocked) or secret(Path(name)):
            raise ValueError(f"禁止编辑第三方或凭据: {name}")
    with project_lock(root, ".ai-project/tasks.lock"):
        if directory.exists():
            raise ValueError("任务 ID 已存在，请 resume，禁止重新建立基线")
        for path in task_state_paths(root):
            if path.parent.name.startswith(".init-"):
                continue
            other = read_json(path)
            if other["status"] not in {"closed", "cancelled"} and set(files).intersection(other["allowedFiles"]):
                raise ValueError(f"编辑范围与活动任务冲突: {other['taskId']}")
        captured = capture_snapshot(root)
        baseline = captured["paths"]
        dirty = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        chunks = dirty.split("\0")
        dirty_names, position = [], 0
        while position < len(chunks):
            entry = chunks[position]
            position += 1
            if not entry:
                continue
            dirty_names.append(entry[3:])
            if "R" in entry[:2] or "C" in entry[:2]:
                if position < len(chunks) and chunks[position]:
                    dirty_names.append(chunks[position])
                    position += 1
        dirty_names = project_relative_paths(root, dirty_names)
        if set(files).intersection(dirty_names):
            raise ValueError("允许编辑文件已有用户修改；请先人工确认边界，不能把整文件归入新任务")
        state = {"schemaVersion": "1.0", "taskId": task_id, "owner": owner, "identity": identity, "baseRevision": git(root, "rev-parse", "HEAD") or "not-applicable", "request": request, "allowedFiles": files, "protectedGlobs": blocked, "baseline": baseline, "readCoverage": captured["coverage"], "checkpoint": baseline, "baselineDirtyStatus": dirty_names, "status": "active", "completed": [], "todo": [], "blockers": [], "nextAction": "实现声明的最小任务范围", "completion": {name: "waiting-user" if name == "realDevice" else "not-run" for name in DIMENSIONS}, "updatedAt": now()}
        # 先完整生成未发布目录，再原子发布；断线不会留下“有 baseline 无 state”的任务。
        directory.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".init-", dir=directory.parent))
        try:
            if read_policy is not None:
                atomic_json(staging / 'read-policy.json', read_policy)
            atomic_json(staging / "baseline.json", {"taskId": task_id, "identity": identity, "paths": baseline, "coverage": captured["coverage"]})
            atomic_json(staging / "state.json", state)
            write_handoff(staging, state)
            os.replace(staging, directory)
        finally:
            if staging.exists():
                for path in staging.iterdir():
                    path.unlink()
                staging.rmdir()
        return state


def verified_upgrade_paths(root: Path, state: dict, acceptance: dict, current: dict) -> set[str]:
    """逐项复核已授权事务链；返回仅属于该链且仍与最终内容匹配的规则路径。"""
    boundary = ReadBoundary(root)
    if acceptance.get('projectRoot') != str(root) or acceptance.get('taskId') != state['taskId']:
        raise ValueError('升级承接项目或任务不匹配')
    if acceptance.get('baselineSha256') != digest(state['baseline']):
        raise ValueError('升级承接的不可变基线不匹配')
    baseline_record = read_json(task_dir(root, state['taskId']) / 'baseline.json')
    if baseline_record.get('taskId') != state['taskId'] or baseline_record.get('paths') != state['baseline'] or baseline_record.get('identity') != state['identity']:
        raise ValueError('任务原始基线不匹配')
    if not acceptance.get('confirmedBy') or not acceptance.get('authorizationSource'):
        raise ValueError('升级承接缺少授权来源')
    records = acceptance.get('transactions')
    if not isinstance(records, list) or not records:
        raise ValueError('缺少升级事务链')
    pins = acceptance.get('trustedSuites')
    if not isinstance(pins, list) or len(pins) != 3 or any(not isinstance(x, dict) or set(x) != {'version', 'suiteRoot', 'suiteSha256'} for x in pins):
        raise ValueError('必须提供三个独立核验的套件来源')
    pinned = {x['version']: x for x in pins}
    if set(pinned) != {'2.5.2', '2.6.1', '2.6.2-ruler-task-handoff'}: raise ValueError('套件来源版本不符')
    manifest_name = '.ai-project/framework-installation.json'
    lifecycle_name = '.ai-project/lifecycle.json'
    anchor = acceptance.get('anchor', {})
    anchor_id = anchor.get('id', '')
    if not re.fullmatch(r'[0-9a-f]{32}', anchor_id): raise ValueError('缺少已核验的起点事务')
    anchor_bytes = boundary.read_bytes(f'.ai-project/upgrades/{anchor_id}/transaction.json')
    if hashlib.sha256(anchor_bytes).hexdigest() != anchor.get('sha256'): raise ValueError('起点事务摘要不符')
    anchor_tx = json.loads(anchor_bytes)
    if anchor_tx.get('projectRoot') != str(root) or anchor_tx.get('status') != 'complete' or anchor_tx.get('planned') != anchor_tx.get('after'):
        raise ValueError('起点事务无效')
    previous_after = None
    baseline = state['baseline']
    initial = {}
    final = {}
    seen = set()
    for item in records:
        txid = item.get('id', '')
        if not re.fullmatch(r'[0-9a-f]{32}', txid) or txid in seen:
            raise ValueError('事务 ID 无效或重复')
        seen.add(txid)
        raw = boundary.read_bytes(f'.ai-project/upgrades/{txid}/transaction.json')
        if hashlib.sha256(raw).hexdigest() != item.get('sha256'):
            raise ValueError('升级事务内容变化')
        tx = json.loads(raw)
        before, planned, after = tx['before'], tx['planned'], tx['after']
        if tx.get('projectRoot') != str(root) or tx.get('status') != 'complete' or set(before) != set(planned) or set(after) != set(planned) or after != planned:
            raise ValueError('升级事务未完成或内容不一致')
        old_manifest, new_manifest = json.loads(before[manifest_name]), json.loads(after[manifest_name])
        trusted_ios_manifest(old_manifest); trusted_ios_manifest(new_manifest)
        if 'contentSha256' in new_manifest: raise ValueError('升级安装清单有额外字段')
        for manifest in (old_manifest, new_manifest):
            pin = pinned[manifest['suiteVersion']]
            if manifest['suiteRoot'] != pin['suiteRoot'] or manifest['suiteSha256'] != pin['suiteSha256']:
                raise ValueError('套件来源与人工核验摘要不符')
        if (old_manifest['suiteVersion'], new_manifest['suiteVersion']) not in {
            ('2.5.2', '2.6.1'), ('2.6.1', '2.6.2-ruler-task-handoff')
        }:
            raise ValueError('未核验的版本转换')
        old_life, new_life = json.loads(before[lifecycle_name]), json.loads(after[lifecycle_name])
        expected_life = dict(old_life); expected_life['frameworkVersion'] = new_manifest['suiteVersion']
        if old_life.get('frameworkVersion') != old_manifest['suiteVersion'] or new_life != expected_life:
            raise ValueError('升级事务改变了生命周期非版本字段')
        old_managed = {x['path']: x for x in old_manifest['managedFiles']}
        new_managed = {x['path']: x for x in new_manifest['managedFiles']}
        if len(old_managed) != len(old_manifest['managedFiles']) or len(new_managed) != len(new_manifest['managedFiles']):
            raise ValueError('安装清单路径重复')
        managed = set(old_managed) | set(new_managed)
        if set(before) != managed | {manifest_name, lifecycle_name}:
            raise ValueError('事务包含非受管文件')
        if previous_after is not None and any(before.get(n) != previous_after.get(n) for n in set(before) | set(previous_after)):
            raise ValueError('升级事务链不连续')
        if previous_after is None:
            if anchor_tx['after'].get(manifest_name) != before[manifest_name] or anchor_tx['after'].get(lifecycle_name) != before[lifecycle_name]:
                raise ValueError('起点事务与首个升级事务不连续')
            old_identity = {'version': old_manifest['suiteVersion'], 'managedSha256': digest(old_manifest['managedFiles'])}
            evidence = [v.get('binding', {}).get('rulesIdentity') for v in state['completion'].values() if isinstance(v, dict)]
            if old_identity != acceptance.get('previousRulesIdentity') or old_identity not in evidence:
                raise ValueError('原规则身份缺少任务证据')
        for name in managed:
            if not name.startswith('.codex-rules/') or safe_path(root, name) != root / name:
                raise ValueError('事务受管路径越界')
            old_hash = hashlib.sha256(before[name].encode()).hexdigest() if before[name] is not None else None
            new_hash = hashlib.sha256(after[name].encode()).hexdigest() if after[name] is not None else None
            if old_hash != (old_managed[name]['installedSha256'] if name in old_managed else None) or new_hash != (new_managed[name]['installedSha256'] if name in new_managed else None):
                raise ValueError('事务文件与安装清单不匹配')
            if name not in initial:
                initial[name] = old_hash
            final[name] = new_hash
        previous_after = after
    if any(baseline.get(name) != value for name, value in initial.items()):
        raise ValueError('事务起点与任务基线不匹配')
    if any(current.get(name) != value for name, value in final.items()):
        raise ValueError('当前规则与事务终点不匹配')
    if boundary.read_text(manifest_name) != previous_after[manifest_name] or boundary.read_text(lifecycle_name) != previous_after[lifecycle_name]:
        raise ValueError('当前安装清单或生命周期不匹配')
    if rules_identity(root) != acceptance.get('rulesIdentity'):
        raise ValueError('最终规则身份不匹配')
    return {name for name in final if baseline.get(name) != current.get(name)}


@task_scoped
def accept_framework_upgrade(root: Path, task_id: str, transaction_ids: list[str], pending_path: str,
                             old_hash: str, new_hash: str, confirmed_by: str, authorization_source: str,
                             apply: bool = False, preview_hash: str | None = None,
                             anchor_id: str = '', anchor_sha256: str = '', trusted_suites: list | None = None) -> dict:
    """承接完整升级链；待确认 UI 只用于核对，不随升级承接而放行。"""
    root = root.resolve()
    if not confirmed_by.strip() or not authorization_source.strip():
        raise ValueError('缺少确认人或授权来源')
    if not re.fullmatch(r'[0-9a-f]{32}', anchor_id or '') or not re.fullmatch(r'[0-9a-f]{64}', anchor_sha256 or ''):
        raise ValueError('必须提供人工核验的起点事务 ID 和摘要')
    if not re.fullmatch(r'.+\.xcodeproj/project\.xcworkspace/xcuserdata/[^/]+\.xcuserdatad/UserInterfaceState\.xcuserstate', pending_path):
        raise ValueError('待确认路径不是 Xcode 用户窗口状态')
    if not all(re.fullmatch(r'[0-9a-f]{64}', h or '') for h in (old_hash, new_hash)) or old_hash == new_hash:
        raise ValueError('待确认摘要无效')
    directory = task_dir(root, task_id)
    with contextlib.ExitStack() as locks:
        if apply:
            locks.enter_context(project_lock(root, '.ai-project/framework-update.lock'))
            locks.enter_context(project_lock(root, f'.ai-project/tasks/{task_id}/verification.lock'))
            locks.enter_context(project_lock(root, '.ai-project/tasks.lock'))
        state = read_json(directory / 'state.json')
        original = read_json(directory / 'baseline.json')
        if state['status'] in {'closed', 'cancelled'} or confirmed_identity(root) != state['identity']:
            raise ValueError('任务已结束或项目身份变化')
        if original.get('taskId') != task_id or original.get('paths') != state['baseline'] or original.get('identity') != state['identity']:
            raise ValueError('不可变基线不匹配')
        if pending_path in state['allowedFiles'] or match(pending_path, state['protectedGlobs']) or state['baseline'].get(pending_path) != old_hash:
            raise ValueError('待确认 UI 的旧摘要或范围不符')
        boundary = ReadBoundary(root)
        if hashlib.sha256(boundary.read_bytes(pending_path)).hexdigest() != new_hash:
            raise ValueError('待确认 UI 当前摘要不符')
        records = []
        for txid in transaction_ids:
            if not re.fullmatch(r'[0-9a-f]{32}', txid): raise ValueError('事务 ID 无效')
            data = boundary.read_bytes(f'.ai-project/upgrades/{txid}/transaction.json')
            records.append({'id': txid, 'sha256': hashlib.sha256(data).hexdigest()})
        if not records: raise ValueError('缺少升级事务链')
        first = boundary.read_json(f".ai-project/upgrades/{records[0]['id']}/transaction.json")
        old_manifest = json.loads(first['before']['.ai-project/framework-installation.json'])
        current = tree_snapshot(root)
        acceptance = {'projectRoot': str(root), 'taskId': task_id, 'baselineSha256': digest(state['baseline']),
                      'transactions': records, 'rulesIdentity': rules_identity(root), 'confirmedBy': confirmed_by,
                      'authorizationSource': authorization_source, 'snapshotSha256': digest(current),
                      'pendingMetadata': {'path': pending_path, 'oldSha256': old_hash, 'newSha256': new_hash},
                      'anchor': {'id': anchor_id, 'sha256': anchor_sha256},
                      'trustedSuites': trusted_suites,
                      'previousRulesIdentity': {'version': old_manifest['suiteVersion'], 'managedSha256': digest(old_manifest['managedFiles'])}}
        existing = state.get('acceptedFrameworkUpgrade')
        if existing:
            if existing != acceptance: raise ValueError('已有不同升级承接记录')
            verified_upgrade_paths(root, state, existing, current)
            return {'status': 'already-accepted', 'acceptance': existing}
        upgraded = verified_upgrade_paths(root, state, acceptance, current)
        other = [n for n in set(current) | set(state['baseline']) if current.get(n) != state['baseline'].get(n)
                 and n not in state['allowedFiles'] and n not in upgraded and n != pending_path]
        if other: raise ValueError(f'存在其他范围外变化: {sorted(other)}')
        preview = digest({'state': digest(state), 'snapshot': digest(current), 'acceptance': acceptance,
                          'pendingPath': pending_path, 'oldSha256': old_hash, 'newSha256': new_hash})
        if not apply: return {'status': 'dry-run', 'acceptance': acceptance, 'previewSha256': preview, 'writes': []}
        if preview_hash != preview: raise ValueError('预览已变化或摘要不匹配')
        backup = directory / 'upgrade-confirmations' / (preview + '.json')
        boundary.require(backup.relative_to(root).as_posix())
        task_fd = secure_fd(root, directory.relative_to(root).as_posix(), directory=True)
        try:
            try: os.mkdir('upgrade-confirmations', 0o700, dir_fd=task_fd)
            except FileExistsError: pass
            audit_fd = os.open('upgrade-confirmations', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=task_fd)
            try:
                tempname = '.pending-' + os.urandom(16).hex()
                try:
                    fd = os.open(tempname, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=audit_fd)
                    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                        json.dump({'stateBefore': state, 'acceptance': acceptance, 'previewSha256': preview}, stream, ensure_ascii=False, indent=2)
                        stream.flush(); os.fsync(stream.fileno())
                    os.link(tempname, backup.name, src_dir_fd=audit_fd, dst_dir_fd=audit_fd, follow_symlinks=False)
                    os.fsync(audit_fd)
                except FileExistsError:
                    stored = boundary.read_json(backup.relative_to(root).as_posix())
                    if stored != {'stateBefore': state, 'acceptance': acceptance, 'previewSha256': preview}: raise ValueError('恢复备份内容不符')
                finally:
                    try: os.unlink(tempname, dir_fd=audit_fd)
                    except FileNotFoundError: pass
            finally: os.close(audit_fd)
        finally: os.close(task_fd)
        refreshed = tree_snapshot(root)
        if digest(refreshed) != digest(current): raise ValueError('承接期间快照变化')
        verified_upgrade_paths(root, state, acceptance, refreshed)
        proposed = json.loads(json.dumps(state)); proposed['acceptedFrameworkUpgrade'] = acceptance
        for item in proposed['completion'].values():
            if isinstance(item, dict) and item.get('status') == 'passed': item['status'] = 'stale'
        atomic_json(directory / 'state.json', proposed)
        try: write_handoff(directory, proposed)
        except OSError: pass
        return {'status': 'accepted', 'acceptance': acceptance, 'backup': str(backup)}


@task_scoped
def check_scope(root: Path, state: dict) -> dict:
    """比较当前真实文件与基线；禁止依赖修改、越界和覆盖已有修改，返回差异或抛错。"""
    # 统一 Mac 目录别名，避免把本任务误认成另一个任务或项目。
    root = root.resolve()
    if state.get('readCoverage') != ReadBoundary(root).coverage():
        raise ValueError('项目或任务读取范围已变化，旧基线失效；禁止自动扩大范围恢复')
    current = tree_snapshot(root)
    changed = sorted(name for name in set(current) | set(state["baseline"]) if current.get(name) != state["baseline"].get(name))
    # 其他任务的文件允许保持独立变化；本任务只声明自己的改动，保护范围全局检查。
    forbidden = [name for name in changed if match(name, state["protectedGlobs"])]
    if forbidden:
        raise ValueError(f"禁改范围发生变化: {forbidden}")
    # 与本任务并行的合法范围只用于排除，不能据此认领别人的修改。
    other_files = set()  # 只排除仍归其他任务所有、或已关闭且保持检查点内容的文件。
    for path in task_state_paths(root):
        if path.parent.name.startswith(".init-"):
            continue
        other = read_json(path)
        if other["taskId"] != state["taskId"]:
            for name in other["allowedFiles"]:
                if other["status"] not in {"closed", "cancelled"} or current.get(name) == other["checkpoint"].get(name):
                    other_files.add(name)
    acknowledged = state.get('acknowledgedMetadata', {})
    upgrades = verified_upgrade_paths(root, state, state['acceptedFrameworkUpgrade'], current) if 'acceptedFrameworkUpgrade' in state else set()
    unexpected = [name for name in changed if name not in state["allowedFiles"] and name not in other_files and name not in upgrades and not (name in acknowledged and state['baseline'].get(name) == acknowledged[name].get('oldSha256') and current.get(name) == acknowledged[name].get('newSha256'))]
    if unexpected:
        raise ValueError(f"范围外或原有用户文件发生变化，暂停人工核对: {unexpected}")
    return {"changedFiles": [name for name in changed if name in state["allowedFiles"]], "current": current, "coverage": ReadBoundary(root).coverage()}


@task_scoped
def acknowledge_metadata(root: Path, task_id: str, path: str, old_hash: str, new_hash: str, reason: str, apply: bool = False, preview_hash: str | None = None, confirmed_by: str = '', authorization_source: str = '') -> dict:
    """确认精确 Xcode UI 元数据；保留原始基线，拒绝任何额外越界变化。"""
    root = root.resolve()
    if not re.fullmatch(r'.+\.xcodeproj/project\.xcworkspace/xcuserdata/[^/]+\.xcuserdatad/UserInterfaceState\.xcuserstate', path):
        raise ValueError('仅允许精确的 Xcode UserInterfaceState 路径')
    if not reason.strip() or not confirmed_by.strip() or not authorization_source.strip() or not all(re.fullmatch(r'[0-9a-f]{64}', value or '') for value in (old_hash, new_hash)) or old_hash == new_hash:
        raise ValueError('必须提供原因、确认人、授权来源及不同的完整旧/新 SHA256')
    directory = task_dir(root, task_id)
    with project_lock(root, '.ai-project/tasks.lock') if apply else contextlib.nullcontext():
        state = read_json(directory / 'state.json')
        if state['status'] in {'closed', 'cancelled'} or confirmed_identity(root) != state['identity']:
            raise ValueError('任务已结束或项目身份变化')
        if path in state['allowedFiles'] or match(path, state['protectedGlobs']):
            raise ValueError('不能确认业务编辑范围或受保护路径')
        original = read_json(directory / 'baseline.json')
        if original.get('taskId') != task_id or original.get('paths') != state['baseline'] or original.get('identity') != state['identity']:
            raise ValueError('原始基线与任务状态不一致')
        if state['baseline'].get(path) != old_hash:
            raise ValueError('旧摘要与不可变基线不一致')
        actual = hashlib.sha256(ReadBoundary(root).read_bytes(path)).hexdigest()
        if actual != new_hash:
            raise ValueError('当前文件摘要与确认值不一致')
        if path in state.get('acknowledgedMetadata', {}):
            raise ValueError('该路径已有确认记录；不得覆盖或重复确认')
        proposed = json.loads(json.dumps(state))
        entry = {'oldSha256': old_hash, 'newSha256': new_hash, 'reason': reason, 'confirmedBy': confirmed_by, 'authorizationSource': authorization_source, 'taskId': task_id, 'path': path, 'at': now()}
        proposed.setdefault('acknowledgedMetadata', {})[path] = entry
        scoped = check_scope(root, proposed)
        upgrades = verified_upgrade_paths(root, state, state['acceptedFrameworkUpgrade'], scoped['current']) if 'acceptedFrameworkUpgrade' in state else set()
        other = [name for name in set(scoped['current']) | set(state['baseline']) if scoped['current'].get(name) != state['baseline'].get(name) and name not in state['allowedFiles'] and name not in upgrades and name != path]
        if other:
            raise ValueError(f'存在其他范围外变化，拒绝确认: {sorted(other)}')
        preview = digest({'projectRoot': str(root), 'taskId': task_id, 'path': path, 'oldSha256': old_hash, 'newSha256': new_hash, 'reason': reason, 'confirmedBy': confirmed_by, 'authorizationSource': authorization_source, 'stateSha256': digest(state), 'snapshotSha256': digest(scoped['current'])})
        # 验证报告仍绑定真实当前文件；已有结果显式降为 stale，绝不重写旧报告。
        for item in proposed['completion'].values():
            if isinstance(item, dict) and item.get('status') == 'passed':
                item['status'] = 'stale'
        if not apply:
            return {'status': 'dry-run', 'acknowledgement': entry, 'previewSha256': preview, 'writes': []}
        if preview_hash != preview:
            raise ValueError('预览摘要缺失或不匹配；任务、范围、原因或实际快照已变化')
        backup = directory / 'metadata-confirmations' / (digest(entry) + '.json')
        ReadBoundary(root).require(backup.relative_to(root).as_posix())
        task_fd = secure_fd(root, directory.relative_to(root).as_posix(), directory=True)
        try:
            try:
                os.mkdir('metadata-confirmations', 0o700, dir_fd=task_fd)
            except FileExistsError:
                pass
            audit_fd = os.open('metadata-confirmations', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=task_fd)
            try:
                fd = os.open(backup.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=audit_fd)
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump({'stateBefore': state, 'acknowledgement': entry}, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                os.close(audit_fd)
        finally:
            os.close(task_fd)
        # 备份后再核对，避免确认期间文件再次变化。
        refreshed = check_scope(root, proposed)
        other = [name for name in set(refreshed['current']) | set(state['baseline']) if refreshed['current'].get(name) != state['baseline'].get(name) and name not in state['allowedFiles'] and name not in upgrades and name != path]
        if other:
            raise ValueError(f'备份期间出现其他范围外变化: {sorted(other)}')
        if digest(refreshed['current']) != digest(scoped['current']):
            raise ValueError('预览后实际快照变化；需要重新审查')
        atomic_json(directory / 'state.json', proposed)
        try:
            write_handoff(directory, proposed)
            handoff = 'updated'
        except OSError:
            handoff = 'stale-rebuild-from-state'
        return {'status': 'acknowledged', 'acknowledgement': entry, 'backup': str(backup), 'handoff': handoff}


@task_scoped
def resume(root: Path, task_id: str) -> dict:
    """只读恢复检查；身份/HEAD/检查点文件变化时拒绝自动继续，不把磁盘状态默认为已验证。"""
    # 统一 Mac 目录别名，避免把本任务误认成另一个任务或项目。
    root = root.resolve()
    state = read_json(task_dir(root, task_id) / "state.json")
    if confirmed_identity(root) != state["identity"] or (git(root, "rev-parse", "HEAD") or "not-applicable") != state["baseRevision"]:
        raise ValueError("恢复身份或 Git revision 不符，需人工重新核对任务")
    current = check_scope(root, state)["current"]
    stale = [name for name in state["allowedFiles"] if current.get(name) != state["checkpoint"].get(name)]
    if stale:
        raise ValueError(f"断线后文件与检查点不符，先 review/checkpoint 确认: {stale}")
    # 恢复是只读查询；返回时降级失效证据，不把旧 passed 继续显示为当前通过。
    for item in state["completion"].values():
        if isinstance(item, dict) and item.get("binding") != binding(root, task_id, item["binding"]["profile"], item["binding"].get("sliceId")):
            item["status"] = "stale"
    return state


def write_handoff(directory: Path, state: dict) -> None:
    """从机器状态生成中文交接视图；state.json 是唯一事实源，视图可在中断后重建。"""
    text = "# 任务交接\n\n" + "\n".join(f"- {key}: {json.dumps(state[key], ensure_ascii=False)}" for key in ("taskId", "owner", "identity", "baseRevision", "request", "allowedFiles", "status", "completed", "todo", "blockers", "nextAction", "completion", "readCoverage", "updatedAt")) + "\n"
    atomic_text(directory / "handoff.md", text)


@task_scoped
def checkpoint(root: Path, task_id: str, completed: list, todo: list, blockers: list, next_action: str, status: str = "active") -> dict:
    """人工核对后保存检查点并更新交接。

    completed/todo/blockers 分别为已完成、待办和阻塞列表，next_action 是一个下一步；
    status 可标记等待权限，但不授予权限或执行后续动作。返回状态，原子写 state 和派生视图。
    """
    # 统一 Mac 目录别名，避免把本任务误认成另一个任务或项目。
    root = root.resolve()
    directory = task_dir(root, task_id)
    with project_lock(root, ".ai-project/tasks.lock"):
        state = read_json(directory / "state.json")
        if state["status"] in {"closed", "cancelled"}:
            raise ValueError("终结任务不能通过 checkpoint 重开；请用新任务 ID 核对新范围")
        for path in task_state_paths(root):
            if path.parent.name.startswith(".init-") or path.parent == directory:
                continue
            other = read_json(path)
            if other["status"] not in {"closed", "cancelled"} and set(other["allowedFiles"]).intersection(state["allowedFiles"]):
                raise ValueError("任务编辑所有权冲突，暂停保存检查点")
        if confirmed_identity(root) != state["identity"]:
            raise ValueError("身份变化，不能保存为同一任务检查点")
        scoped = check_scope(root, state)
        for item in state["completion"].values():
            if isinstance(item, dict) and item.get("binding") != binding(root, task_id, item["binding"]["profile"], item["binding"].get("sliceId")):
                item["status"] = "stale"
        state.update(checkpoint=scoped["current"], completed=completed, todo=todo, blockers=blockers, nextAction=next_action, status=status, updatedAt=now())
        state["completion"]["codeModified"] = "modified" if scoped["changedFiles"] else "unchanged" if scoped["coverage"]["status"] == "complete" else "unchanged-in-readable-scope"
        atomic_json(directory / "state.json", state)
        write_handoff(directory, state)
        return state


@task_scoped
def binding(root: Path, task_id: str, profile: str, slice_id: str | None = None) -> dict:
    """生成证据绑定；核对范围后记录真实文件摘要、身份与 HEAD，任何内容变化使旧证据失效。"""
    # 统一 Mac 目录别名，避免把本任务误认成另一个任务或项目。
    root = root.resolve()
    state = read_json(task_dir(root, task_id) / "state.json")
    # 同一次绑定内复用刚核验的规则；下一绑定（包括执行后）始终重新读取。
    rules = rules_identity(root)
    identity = confirmed_identity(root, validated_rules=rules)
    if identity != state["identity"]:
        raise ValueError("任务与当前项目身份不一致")
    files = check_scope(root, state)["current"]
    return {"taskId": task_id, "sliceId": slice_id, "projectRoot": str(root.resolve()), "identitySha256": digest(identity), "revision": git(root, "rev-parse", "HEAD") or "not-applicable", "codeSha256": digest(files), "profile": profile, "rulesIdentity": rules, "readCoverage": ReadBoundary(root).coverage(), "verificationContractSha256": digest(read_json(root / ".ai-project/verification-commands.json")) if ReadBoundary(root).exists(".ai-project/verification-commands.json") else None}


def validate_report(root: Path, report: dict, task_id: str, profile: str, slice_id: str | None = None, allow_partial: bool = False) -> None:
    """验证当前任务的通过证据；拒绝错任务/错等级/旧代码/缺命令及未来时间；不证明人工陈述真伪。"""
    current = binding(root, task_id, profile, slice_id)
    expected_status = "passed" if current["readCoverage"]["status"] == "complete" else "partial"
    if (expected_status == "partial" and (not allow_partial or report.get("kind") != "configuration")) or report.get("status") != expected_status or report.get("binding") != current:
        raise ValueError("验证报告失败、过期或不属于当前任务/代码/profile")
    started = datetime.fromisoformat(report["startedAt"])
    finished = datetime.fromisoformat(report["finishedAt"])
    if not started.tzinfo or not finished.tzinfo or started > finished or finished > datetime.now(timezone.utc):
        raise ValueError("验证报告时间无效")
    if report.get("kind") == "configuration":
        expected_checks = [{"name": "identity-rules-and-allowed-file-baseline", "status": "passed", "changedFiles": check_scope(root, read_json(task_dir(root, task_id) / "state.json"))["changedFiles"]}]
        if profile != "D0" or report.get("coverage") != current["readCoverage"] or report.get("executionStatus") != "passed" or report.get("checks") != expected_checks:
            raise ValueError("配置报告范围、等级或检查内容不完整")
        return
    commands = report.get("commands", [])
    if not commands or any(not item.get("argv") or item.get("exitCode") != 0 for item in commands):
        raise ValueError("通过报告必须包含实际执行命令及成功退出状态")


def check_operation_hold(root: Path, contract: dict) -> None:
    """操作暂停只约束对应操作，不推导路径禁读；用途标注不能替代平台实际审批。"""
    boundary = ReadBoundary(root)
    path = '.ai-project/operation-holds.json'
    if not boundary.exists(path):
        return
    active = [item for item in boundary.read_json(path).get('operations', []) if item.get('status') == 'hold']
    if not active:
        return
    operation = contract.get('operationId')
    if not operation:
        raise ValueError('存在历史操作暂停；先核对本次具体命令用途并声明 operationId，不自动重试旧拒绝操作。这不是项目路径禁读')
    if operation in {item.get('operationId') for item in active}:
        raise ValueError('该具体操作仍在历史平台拒绝暂停中；项目目录授权不能解除平台实际限制')


@task_scoped
def run_verification(root: Path, task_id: str, profile: str, kind: str, argv: list[str], report_path: Path, timeout: int = 900) -> dict:
    """执行项目明确确认的验证命令并绑定真实代码。

    task_id/profile 标识任务与等级，kind 区分编译/自动测试，argv 为精确命令参数；
    report_path 是报告输出，timeout 单位秒。返回报告并更新对应完成维度；
    超时/无法执行记录 blocked，失败/执行中源码变化不会产生 passed，提交与推送不属于本函数的完成维度。
    """
    # 统一 Mac 目录别名，避免把本任务误认成另一个任务或项目。
    root = root.resolve()
    if not argv or kind not in {"compiled", "automatedTests"}:
        raise ValueError("验证仅支持编译或自动测试命令")
    if not report_path.resolve().is_relative_to(task_dir(root, task_id).resolve()):
        raise ValueError("验证报告只能写到本任务目录，不能覆盖业务/依赖文件")
    require_complete(root)  # 子进程无法由本工具强制遵守部分白名单，必须在执行前拒绝。
    contracts = read_json(root / ".ai-project/verification-commands.json").get("commands", [])
    contract = next((item for item in contracts if item.get("confirmed") is True and item.get("argv") == argv and item.get("kind") == kind and profile in item.get("profiles", [])), None)
    if contract is None:
        raise ValueError("命令、验证维度和 profile 尚未在项目中明确确认")
    check_operation_hold(root, contract)
    with project_lock(root, f".ai-project/tasks/{task_id}/verification.lock"):
        if read_json(task_dir(root, task_id) / "state.json")["status"] != "active":
            raise ValueError("任务正在等待权限、阻塞或已关闭，不能自动执行验证")
        before = binding(root, task_id, profile)
        started = now()
        # 超时和不可执行也保存本次结果，不能留下上一轮 passed 冒充当前尝试。
        failure = None
        try:
            result = subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False, timeout=timeout)
            exit_code = result.returncode
            output_digest = digest({"stdout": result.stdout, "stderr": result.stderr})
        except (OSError, subprocess.TimeoutExpired) as error:
            exit_code = None
            output_digest = digest(str(error))
            failure = type(error).__name__
        try:
            after = binding(root, task_id, profile)
        except ValueError as error:
            after = None
            failure = str(error)
        # 构建若修改源码，不能用执行前输入的结果声明当前代码通过。
        status = "blocked" if failure else "passed" if exit_code == 0 and before == after else "failed"
        report = {"schemaVersion": "1.0", "binding": before, "startedAt": started, "finishedAt": now(), "status": status, "reason": failure, "kind": kind, "commands": [{"argv": argv, "cwd": str(root), "exitCode": exit_code}], "outputSha256": output_digest, "coverage": before["readCoverage"]}
        atomic_json(task_dir(root, task_id) / "verification-history" / (started.replace(":", "-") + ".json"), report)
        atomic_json(report_path, report)
        with project_lock(root, ".ai-project/tasks.lock"):
            state = read_json(task_dir(root, task_id) / "state.json")
            state["completion"][kind] = {"status": report["status"], "report": str(report_path), "binding": before}
            if report["status"] == "blocked":
                state["status"] = "blocked"
                state["blockers"].append({"reason": report["reason"], "report": str(report_path)})
                state["nextAction"] = "核对执行环境、权限或范围冲突后保存检查点，再重新验证"
            state["updatedAt"] = now()
            atomic_json(task_dir(root, task_id) / "state.json", state)
            write_handoff(task_dir(root, task_id), state)
        return report


@task_scoped
def verify_configuration(root: Path, task_id: str, profile: str = "D0") -> dict:
    """仅在进程内核对当前身份/规则/允许文件；不执行命令，部分覆盖保持 partial。"""
    root = root.resolve()
    if profile != "D0":
        raise ValueError("进程内配置检查仅支持 D0，不能替代功能/发布验证")
    directory = task_dir(root, task_id)
    with project_lock(root, f".ai-project/tasks/{task_id}/verification.lock"):
        state = read_json(directory / "state.json")
        if state["status"] != "active":
            raise ValueError("终结或等待中的任务不能自动验证")
        started = now()
        before = binding(root, task_id, profile)
        observed = check_scope(root, state)
        after = binding(root, task_id, profile)
        if before != after:
            raise ValueError("配置检查期间允许文件变化，阻塞报告")
        status = "passed" if before["readCoverage"]["status"] == "complete" else "partial"
        report = {"schemaVersion": "1.1", "binding": before, "startedAt": started, "finishedAt": now(), "status": status, "executionStatus": "passed", "kind": "configuration", "coverage": before["readCoverage"], "checks": [{"name": "identity-rules-and-allowed-file-baseline", "status": "passed", "changedFiles": observed["changedFiles"]}], "limitations": before["readCoverage"]["uninspected"]}
        report_path = directory / "configuration.json"
        atomic_json(report_path, report)
        with project_lock(root, ".ai-project/tasks.lock"):
            state = read_json(directory / "state.json")
            state["completion"]["configuration"] = {"status": status, "report": str(report_path), "binding": before}
            atomic_json(directory / "state.json", state)
            write_handoff(directory, state)
        return report



def main() -> int:
    """提供身份/init/resume/checkpoint/verify 命令；预期冲突以 blocked 和非零退出表达。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("identity", "init", "resume", "check", "checkpoint", "verify", "verify-config", "ack-metadata", "accept-framework-upgrade"))
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--task-id")
    parser.add_argument('--task-read-policy', type=Path, help='仅 init 使用的任务级范围文件，不能写入共享项目策略')
    parser.add_argument("--platform", choices=("ios", "harmonyos"))
    parser.add_argument("--write", action="store_true")
    for field in ("bundle-id", "scheme", "product", "module", "build-mode"):
        parser.add_argument("--" + field)
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--protect", action="append", default=[])
    parser.add_argument("--request", default="")
    parser.add_argument("--owner", default="user-session")
    for field in ("completed", "todo", "blocker"):
        parser.add_argument("--" + field, action="append", default=[])
    parser.add_argument("--next", default="核对交接后继续")
    parser.add_argument("--status", choices=("active", "waiting-permission", "blocked", "closed", "cancelled"), default="active")
    parser.add_argument("--profile", default="D2")
    parser.add_argument("--kind", choices=("compiled", "automatedTests"), default="compiled")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--argv", nargs=argparse.REMAINDER)
    parser.add_argument('--old-sha256')
    parser.add_argument('--new-sha256')
    parser.add_argument('--reason', default='')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--preview-sha256')
    parser.add_argument('--confirmed-by', default='')
    parser.add_argument('--authorization-source', default='')
    parser.add_argument('--transaction-id', action='append', default=[])
    parser.add_argument('--anchor-transaction-id', default='')
    parser.add_argument('--anchor-sha256', default='')
    parser.add_argument('--trusted-suite', action='append', default=[])
    args = parser.parse_args()
    root = args.project.expanduser().resolve()
    try:
        if args.command == "identity":
            selection = {"bundleId": args.bundle_id, "scheme": args.scheme, "product": args.product, "module": args.module, "buildMode": args.build_mode}
            result = observe_identity(root, args.platform, selection)
            if args.write:
                if any(field["status"] == "needs-confirmation" for field in result["fields"].values()):
                    raise ValueError("身份存在未知项，请明确确认字段，不允许写成成功")
                with project_lock(root, ".ai-project/tasks.lock"):
                    atomic_json(root / ".ai-project/identity.json", result)
        elif args.command == "init":
            policy = json.loads(bootstrap_bytes(args.task_read_policy.parent.resolve(), args.task_read_policy.name)) if args.task_read_policy else None
            result = begin(root, args.task_id, args.file, args.request, args.owner, args.protect, read_policy=policy)
        elif args.command == "resume":
            result = resume(root, args.task_id)
        elif args.command == "check":
            result = check_scope(root, read_json(task_dir(root, args.task_id) / "state.json"))
            result.pop("current")
        elif args.command == "checkpoint":
            result = checkpoint(root, args.task_id, args.completed, args.todo, args.blocker, args.next, args.status)
        elif args.command == 'ack-metadata':
            if len(args.file) != 1:
                raise ValueError('必须指定且只指定一个 --file')
            result = acknowledge_metadata(root, args.task_id, args.file[0], args.old_sha256, args.new_sha256, args.reason, args.apply, args.preview_sha256, args.confirmed_by, args.authorization_source)
        elif args.command == 'accept-framework-upgrade':
            if len(args.file) != 1:
                raise ValueError('必须指定且只指定一个待确认 UI 文件')
            result = accept_framework_upgrade(root, args.task_id, args.transaction_id, args.file[0], args.old_sha256, args.new_sha256, args.confirmed_by, args.authorization_source, args.apply, args.preview_sha256, args.anchor_transaction_id, args.anchor_sha256, [json.loads(x) for x in args.trusted_suite])
        elif args.command == "verify-config":
            result = verify_configuration(root, args.task_id, args.profile)
        else:
            result = run_verification(root, args.task_id, args.profile, args.kind, args.argv, args.report or task_dir(root, args.task_id) / f"{args.kind}.json")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3 if result.get("status") == "partial" else 1 if result.get("status") in {"failed", "blocked"} else 0
    except (ValueError, OSError, TypeError, KeyError, subprocess.TimeoutExpired) as error:
        coverage = {"status": "blocked", "reason": str(error)}
        try:
            coverage["declaredScope"] = ReadBoundary(root).coverage()
        except (OSError, ValueError, TypeError):
            coverage["declaredScope"] = "unavailable-invalid-or-missing-policy"
        print(json.dumps({"status": "blocked", "error": str(error), "readCoverage": coverage}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
