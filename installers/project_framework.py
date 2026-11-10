#!/usr/bin/env python3
"""Record, audit, and safely update a project's installed framework rules."""

from __future__ import annotations

import argparse
import ast
import contextlib
from contextvars import ContextVar
import fcntl
import os
import re
import stat
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
from task_runtime import RUNTIME_MANAGED_MAPPINGS, atomic_json, atomic_text, safe_path, suite_digest, validate_state, digest
from read_boundary import ReadBoundary, secure_fd, canonical_relative, external_control_bytes, task_only_current



# 安装协议与运行时绑定协议一起演进；不是上游发布版本。
TASK_PROTOCOL = "2"
MANIFEST_RELATIVE = Path(".ai-project/framework-installation.json")
# 子安装进程只复用父进程实际持有的同一锁描述符，不凭环境字符串宣称已停写。
INSTALL_LOCK_ENV = "AI_FRAMEWORK_INSTALL_LOCK_FDS"
LOCK_PATHS = (".ai-project/framework-update.lock", ".ai-project/tasks.lock")
_INSTALL_SCOPE = ContextVar("framework_install_scope", default=None)


MANAGED_MAPPINGS = RUNTIME_MANAGED_MAPPINGS  # 安装与运行时共用同一必需映射，部分清单不能被标成完整协议。



@contextlib.contextmanager
def installation_locks(project: Path):
    """固定 update→tasks 顺序；只复用 inode 相符的继承锁，其他情况安全创建非等待锁。"""
    project = project.resolve()
    boundary = ReadBoundary(project)
    active = _INSTALL_SCOPE.get()
    inherited = os.environ.get(INSTALL_LOCK_ENV)
    descriptors = []
    borrowed = (active is not None and active["projectRoot"] == str(project)) or inherited is not None
    if borrowed:
        supplied = active if active is not None and active["projectRoot"] == str(project) else json.loads(inherited)
        if not isinstance(supplied, dict) or supplied.get("projectRoot") != str(project) or len(supplied.get("fds", [])) != len(LOCK_PATHS):
            raise ValueError("继承安装锁格式或项目根不符")
        descriptors = supplied["fds"]
        if any(type(fd) is not int or fd < 3 for fd in descriptors):
            raise ValueError("继承安装锁描述符无效")
        for relative, descriptor in zip(LOCK_PATHS, descriptors):
            boundary.require(relative)
            expected = secure_fd(project, relative)
            try:
                before, actual = os.fstat(expected), os.fstat(descriptor)
                if (before.st_dev, before.st_ino) != (actual.st_dev, actual.st_ino) or not stat.S_ISREG(actual.st_mode):
                    raise ValueError("继承安装锁属于其他文件")
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(expected)
    try:
        if not borrowed:
            directory = secure_fd(project, ".ai-project", directory=True)
            try:
                for relative in LOCK_PATHS:
                    boundary.require(relative)
                    descriptor = os.open(Path(relative).name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
                    descriptors.append(descriptor)
                    info = os.fstat(descriptor)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError("安装锁必须为单链接普通文件")
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError as error:
                        raise ValueError("安装或任务正在被另一写入进程使用；停止写入后重试") from error
            finally:
                os.close(directory)
        token = _INSTALL_SCOPE.set({"projectRoot": str(project), "fds": descriptors})
        try:
            yield descriptors
        finally:
            _INSTALL_SCOPE.reset(token)
    finally:
        if not borrowed:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


# 有这些任务所有权/执行标记而缺 state 的目录不能用材料审查豁免；手写 handoff 不自动算任务。
TASK_STATE_MARKERS = {"state.json", "baseline.json", "task-only.json", "read-policy.json", "verification.lock", "controlled-writes", "verification-history", "metadata-confirmations", "configuration.json", "compiled.json", "automatedtests.json"}
ARTIFACT_REVIEW_PURPOSE = "preserve-non-owning-legacy-artifacts"
ARTIFACT_LIMITATIONS = ["direct metadata only; attachment bytes and descendant contents are not read", "review records provenance and an observed root; it does not authenticate authorization or prove historical ownership"]


def project_anchor(project: Path) -> dict:
    """绑定规范真实根的文件系统对象；项目复制不能复用其他根的材料审查。"""
    info = os.stat(project)
    return {"projectRoot": str(project), "projectDevice": info.st_dev, "projectInode": info.st_ino}


def metadata_entry(name: str, info, kind: str) -> dict:
    """仅序列化直接条目的身份与变化元数据；不打开附件，不包含访问时间。"""
    return {"name": name, "type": kind, "device": info.st_dev, "inode": info.st_ino, "size": info.st_size, "mode": info.st_mode,
            "mtimeNs": info.st_mtime_ns, "ctimeNs": info.st_ctime_ns, "nlink": info.st_nlink}


def artifact_inventory(project: Path, directory: str) -> dict:
    """只在已授权的精确材料目录枚举并 stat 直接项；拒绝所有持久任务标记与不安全类型。"""
    if task_only_current(project):
        raise ValueError("任务专用范围不能审查或豁免全局历史材料")
    if not isinstance(directory, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", directory):
        raise ValueError("历史材料目录必须是精确的安全直接名称；初始化目录不可豁免")
    boundary = ReadBoundary(project)
    relative = boundary.require(f".ai-project/tasks/{directory}")
    descriptor = secure_fd(project, relative, directory=True)
    try:
        before = metadata_entry(directory, os.fstat(descriptor), "directory")
        def capture():
            """名字先完成规范化与授权，随后才探测元数据；不递归打开子目录。"""
            with os.scandir(descriptor) as entries:
                names = sorted(entry.name for entry in entries)
            normalized = [canonical_relative(name) for name in names]
            if len(normalized) != len(set(normalized)) or any("/" in name for name in normalized):
                raise ValueError("材料直接项规范名称冲突")
            # 整批授权先于任何 entry.stat；未知或禁止条目不探测其元数据。
            for name in normalized:
                boundary.require(f"{relative}/{name}")
            result = []
            for disk_name, name in zip(names, normalized):
                if name.casefold() in TASK_STATE_MARKERS or name.casefold().startswith(".init-"):
                    if name.casefold() == "state.json":
                        state = boundary.read_json(f"{relative}/{name}")
                        validate_state(state, allow_legacy=True)
                        if state["taskId"] != directory or state["identity"]["projectRoot"] != str(project):
                            raise ValueError("材料目录中新状态归属不符")
                    raise ValueError("缺状态材料目录出现持久任务标记或新 state.json，不能审查豁免: " + name)
                info = os.stat(disk_name, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    kind = "file"
                elif stat.S_ISDIR(info.st_mode):
                    kind = "directory"  # 普通子目录只绑定自身；深层内容既不读取也不认证。
                else:
                    raise ValueError("历史材料存在链接、特殊文件或多重硬链接；拒绝豁免")
                result.append(metadata_entry(name, info, kind))
            return result
        entries = capture()
        if capture() != entries or metadata_entry(directory, os.fstat(descriptor), "directory") != before:
            raise ValueError("历史材料直接清单在读取期间变化")
        reopened = secure_fd(project, relative, directory=True)
        try:
            if metadata_entry(directory, os.fstat(reopened), "directory") != before:
                raise ValueError("历史材料目录在核对期间被替换")
        finally:
            os.close(reopened)
        inventory = {"directory": before, "entries": entries}
        return {"directInventorySha256": digest(inventory), "inventory": inventory}
    finally:
        os.close(descriptor)


def review_artifacts(project: Path, directories: list[str]) -> dict:
    """生成只读待审草案；操作者必须逐目录确认，工具绝不自动填 confirmed=true。"""
    project = project.resolve()
    if not isinstance(directories, list) or not directories or len(directories) != len(set(directories)):
        raise ValueError("必须显式选择非空、无重复的历史材料目录列表")
    inventories = {name: artifact_inventory(project, name) for name in sorted(directories)}
    return {"schemaVersion": "1.0", "confirmed": False, "purpose": ARTIFACT_REVIEW_PURPOSE, **project_anchor(project),
            "reviewedBy": "", "authorizationSource": "", "directories": {name: value["directInventorySha256"] for name, value in inventories.items()},
            "directInventories": {name: value["inventory"] for name, value in inventories.items()}, "limitations": list(ARTIFACT_LIMITATIONS)}


def validate_artifact_review(project: Path, review: dict | None) -> dict | None:
    """校验明确审查与实时直接清单；旧材料不因名称、年龄或 router context 被自动豁免。"""
    if review is None:
        return None
    if task_only_current(project):
        raise ValueError("任务专用范围不能使用历史材料审查入口")
    required = {"schemaVersion", "confirmed", "purpose", "projectRoot", "projectDevice", "projectInode", "reviewedBy", "authorizationSource", "directories", "directInventories", "limitations"}
    if not isinstance(review, dict) or set(review) != required or review.get("schemaVersion") != "1.0" or review.get("confirmed") is not True or review.get("purpose") != ARTIFACT_REVIEW_PURPOSE:
        raise ValueError("历史材料审查必须是明确确认的已知格式")
    if any(type(review.get(key)) is not int for key in ("projectDevice", "projectInode")) or any(review.get(key) != value for key, value in project_anchor(project).items()):
        raise ValueError("历史材料审查属于其他项目根或文件系统对象")
    if any(not isinstance(review.get(key), str) or not review[key].strip() for key in ("reviewedBy", "authorizationSource")):
        raise ValueError("历史材料审查缺少审查者或授权来源")
    directories = review["directories"]
    if not isinstance(directories, dict) or not directories or not isinstance(review["directInventories"], dict) or set(review["directInventories"]) != set(directories) or review["limitations"] != ARTIFACT_LIMITATIONS:
        raise ValueError("历史材料审查目录或限制说明无效")
    for name, expected in directories.items():
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("历史材料直接清单摘要无效")
        actual = artifact_inventory(project, name)
        if digest(review["directInventories"][name]) != expected or actual["directInventorySha256"] != expected or actual["inventory"] != review["directInventories"][name]:
            raise ValueError("历史材料清单发生漂移；需要新的逐目录人工审查")
    return review


def load_artifact_review(project: Path, path: Path | None, expected_sha256: str | None) -> dict | None:
    """显式控制 JSON 必须配对字节摘要；项目内遵守原边界，外部逐层拒绝链接。"""
    if task_only_current(project):
        raise ValueError("任务专用范围不能装载全局历史材料审查控制文件")
    if path is None and expected_sha256 is None:
        return None
    if path is None or not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("必须配对提供 --legacy-artifact-review 与完整 --legacy-artifact-review-sha256")
    path = path.expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("历史材料审查控制文件必须使用规范绝对路径")
    if path.is_relative_to(project):
        data = ReadBoundary(project).read_bytes(path.relative_to(project).as_posix())
    else:
        data = external_control_bytes(path, project)
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("历史材料审查控制文件 SHA256 不符")
    review = json.loads(data)
    if not isinstance(review, dict):
        raise ValueError("历史材料审查必须是 JSON 对象")
    return review


def installation_states(project: Path, artifact_review: dict | None = None) -> list[dict]:
    """严格读任务状态；无 state 的材料仅接受当前根、精确直接清单的显式审查，原件不写入。"""
    boundary = ReadBoundary(project)
    review = validate_artifact_review(project, artifact_review)
    reviewed = review["directories"] if review else {}
    boundary.require(".ai-project/tasks")
    try:
        descriptor = secure_fd(project, ".ai-project/tasks", directory=True)
    except FileNotFoundError:
        return []
    try:
        with os.scandir(descriptor) as entries:
            names = sorted(entry.name for entry in entries)
    finally:
        os.close(descriptor)
    result = []
    for task_id in names:
        if task_id.startswith(".init-"):
            raise ValueError("存在未发布的任务初始化记录；先检查中断结果")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", task_id):
            raise ValueError("任务目录存在无法确认的元数据条目")
        relative = f".ai-project/tasks/{task_id}/state.json"
        if not boundary.exists(relative):
            if task_id not in reviewed:
                raise ValueError("任务目录缺少权威 state.json；安装暂停，需逐目录明确材料审查")
            continue
        state = boundary.read_json(relative)
        if not isinstance(state, dict) or state.get("taskId") != Path(relative).parent.name or state.get("status") not in {"active", "blocked", "waiting-permission", "closed", "cancelled"}:
            raise ValueError("任务记录归属或状态无法确认；安装暂停")
        validate_state(state, allow_legacy=True)
        if state["identity"]["projectRoot"] != str(project.resolve()):
            raise ValueError("任务记录属于其他项目根；安装暂停")
        if task_id in reviewed:
            raise ValueError("已审查材料目录出现 task state；审查范围已漂移，禁止忽略所有权")
        result.append(state)
    return result


def manifest_protocol(manifest: dict) -> str:
    """旧清单可只读审计；未知协议与伪装成新协议的不完整清单一律拒绝。"""
    protocol = manifest.get("taskProtocolVersion")
    if manifest.get("schemaVersion") != "1.0" or protocol not in {None, TASK_PROTOCOL} or not isinstance(manifest.get("managedFiles"), list):
        raise ValueError("未知安装清单协议")
    if any(not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ("path", "source", "installedSha256", "sourceSha256")) for item in manifest["managedFiles"]):
        raise ValueError("受管安装记录不完整")
    paths = [item.get("path") for item in manifest["managedFiles"]]
    if len(paths) != len(set(paths)):
        raise ValueError("安装清单受管路径重复")
    if protocol == TASK_PROTOCOL:
        required = dict(MANAGED_MAPPINGS.get(manifest.get("framework"), []))
        actual = {item["path"]: item.get("source") for item in manifest["managedFiles"]}
        if not required or any(actual.get(path) != source for path, source in required.items()):
            raise ValueError("新协议受管文件清单不完整；拒绝混合安装")
    return protocol or "legacy"


def require_offline(offline_ack: str) -> None:
    """停写声明必须明确记录来源；advisory lock 不能证明旧进程或 Xcode 已停止。"""
    if not isinstance(offline_ack, str) or not offline_ack.strip():
        raise ValueError("必须通过 --offline-writers-stopped 记录已停止全部旧/新写入者的确认来源；不能在线热升级")



def require_fresh_installation(project: Path) -> None:
    """首装不能重建旧规则或旧任务基线；缺清单的既有接入须先独立审计/adopt。"""
    boundary = ReadBoundary(project)
    if boundary.exists(MANIFEST_RELATIVE.as_posix()) or installation_states(project):
        raise ValueError("已有安装清单或任务；请审计并离线升级，不得重新 setup")
    for relative in (".codex-rules", ".migration-v2"):
        if not boundary.allowed(relative):
            continue  # 部分授权不探测未知目录，不据此声称整个项目无其他框架。
        boundary.require(relative)
        try:
            descriptor = secure_fd(project, relative, directory=True)
        except FileNotFoundError:
            continue
        os.close(descriptor)
        raise ValueError("已有未登记规则；需要 adopt/audit，拒绝首装覆盖: " + relative)

def setup_new(project: Path, suite: Path, argv: list[str]) -> dict:
    """首装全过程持双锁；已接入或已有任务的目录不能借 setup 绕过升级门禁。"""
    project, suite = project.resolve(), suite.resolve()
    suite_protocol(suite)
    scripts = {suite / "frameworks/ios-daily-development-rules/setup-project.sh", suite / "frameworks/harmonyos-daily-development-rules/setup-project.sh"}
    if not argv or Path(argv[0]).resolve() not in scripts or len(argv) < 2 or Path(argv[1]).resolve() != project:
        raise ValueError("首装子命令必须是本套件的指定项目安装入口")
    with installation_locks(project) as descriptors:
        require_fresh_installation(project)
        environment = dict(os.environ)
        environment[INSTALL_LOCK_ENV] = json.dumps({"projectRoot": str(project), "fds": descriptors})
        result = subprocess.run(argv, env=environment, pass_fds=descriptors, check=False)
        if result.returncode:
            raise ValueError("首装子进程失败；未承诺跨文件原子安装，请审计部分文件后恢复")
    return {"status": "installed", "projectRoot": str(project), "taskProtocolVersion": TASK_PROTOCOL}

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



def suite_protocol(suite: Path) -> str:
    """只解析候选运行器常量，不执行代码；旧/未知源码不能被新安装器标记为协议2。"""
    runtime = suite / "shared/task-routing/task_runtime.py"
    try:
        parsed = ast.parse(runtime.read_text(encoding="utf-8"))
        values = [node.value.value for node in parsed.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "TASK_PROTOCOL" for target in node.targets) and isinstance(node.value, ast.Constant)]
    except (OSError, SyntaxError) as error:
        raise ValueError("目标套件运行器协议无法确认") from error
    if values != [TASK_PROTOCOL]:
        raise ValueError("目标套件为旧或未知协议；拒绝混合安装或降级")
    return TASK_PROTOCOL

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
    legacy: bool = False,
) -> dict:
    """记录受管文件和源码摘要；原子写入安装清单，不覆盖业务事实。"""
    project = project.resolve()
    suite = suite.resolve()
    # 非持久预览不取得锁；写入调用在同进程或继承的双锁内重新核对。
    if persist:
        with installation_locks(project):
            if ReadBoundary(project).exists(MANIFEST_RELATIVE.as_posix()):
                raise ValueError("已有安装清单；record 不能重登记，请 audit/update")
            if installation_states(project):
                raise ValueError("已有任务不能通过 record/adopt 改写安装基线")
            manifest = record_manifest(project, suite, framework, lifecycle, mappings, persist=False, legacy=legacy)
            atomic_json(project / MANIFEST_RELATIVE, manifest)
            return manifest
    # 仅预览已有文件，不把本地漂移静默重置成新基线。
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
    if not legacy:
        suite_protocol(suite)
        manifest["taskProtocolVersion"] = TASK_PROTOCOL
        manifest_protocol(manifest)
        if any(item["installedSha256"] != item["sourceSha256"] for item in managed):
            raise ValueError("新协议首次记录要求每个受管文件与候选源码一致")
    return manifest


def read_manifest(project: Path) -> dict:
    """读取已安装规则清单；缺失时要求先建立可审计基线。"""
    path = project / MANIFEST_RELATIVE
    if not path.is_file():
        raise ValueError(f"framework installation manifest is missing: {path}")
    return ReadBoundary(project).read_json(MANIFEST_RELATIVE.as_posix())


def audit_project(project: Path, suite: Path, artifact_review: dict | None = None) -> dict:
    """只读比较安装、源码和项目规则，返回漂移、更新状态与差异预览。"""
    project = project.resolve()
    suite = suite.resolve()
    suite_protocol(suite)
    manifest = read_manifest(project)
    protocol = manifest_protocol(manifest)
    selected_review = artifact_review if artifact_review is not None else manifest.get("legacyArtifactReview")
    review = json.loads(json.dumps(selected_review)) if selected_review is not None else None
    states = installation_states(project, review)
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
    active_tasks = [state["taskId"] for state in states if state.get("taskProtocolVersion") == TASK_PROTOCOL and state["status"] not in {"closed", "cancelled"}]
    # 终结状态不代表验证子进程已退出；遗留执行须通过独立 release-execution 流程解除。
    pending_tasks = [state["taskId"] for state in states if state.get("pendingExecutions")]
    supported_schemas = runtime_state_schemas((suite / 'shared/task-routing/task_runtime.py').read_text(encoding='utf-8'))
    unsupported_tasks = [state['taskId'] for state in states if state['schemaVersion'] != '1.0' and state['schemaVersion'] not in supported_schemas]
    return {
        "schemaVersion": "1.0",
        "project": str(project),
        "framework": manifest.get("framework"),
        "lifecycle": manifest.get("lifecycle"),
        "installedVersion": manifest.get("suiteVersion"),
        "installedTaskProtocolVersion": protocol,
        "targetTaskProtocolVersion": TASK_PROTOCOL,
        "offlineWritersStoppedRequired": True,
        "taskDataMigration": "explicit-per-task-after-binary-update; task states are never rewritten by installation",
        "legacyArtifactReview": review,
        "legacyArtifactReviewSha256": digest(review) if review is not None else None,
        "activeProtocol2Tasks": active_tasks,
        "pendingExecutionTasks": pending_tasks,
        "targetSupportedStateSchemas": sorted(supported_schemas),
        "unsupportedTaskSchemaTasks": unsupported_tasks,
        "currentSuiteVersion": current_version,
        "updateAvailable": manifest.get("suiteVersion") != current_version or manifest.get("suiteSha256") != suite_digest(suite) or any(item["updateAvailable"] for item in files),
        "safeToUpdate": not local_drift and not active_tasks and not pending_tasks and not unsupported_tasks,
        "suiteContentChanged": manifest.get("suiteSha256") != suite_digest(suite),
        "localDrift": local_drift,
        "managedFiles": files,
    }


def upgrade(project: Path, suite: Path, offline_ack: str = "", artifact_review: dict | None = None) -> dict:
    """锁定并重审计后升级受管文件；保存原文及计划摘要，异常/断线后可显式恢复。"""
    require_offline(offline_ack)
    with installation_locks(project):
        audit = audit_project(project, suite, artifact_review)
        if audit["pendingExecutionTasks"]:
            raise ValueError("任务仍有活跃或遗留验证执行；先核实并显式 release-execution，停写声明不能越过: " + ",".join(audit["pendingExecutionTasks"]))
        if audit["activeProtocol2Tasks"]:
            raise ValueError("新协议活动任务必须先显式终结，不能在线更新规则: " + ",".join(audit["activeProtocol2Tasks"]))
        if audit['unsupportedTaskSchemaTasks']:
            raise ValueError('目标运行器不支持现有任务状态格式（包括2.1），不能通过update降级语义: ' + ','.join(audit['unsupportedTaskSchemaTasks']))
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
        future_manifest = {"schemaVersion": "1.0", "taskProtocolVersion": TASK_PROTOCOL, "suiteVersion": suite_version(suite), "framework": audit["framework"], "lifecycle": audit["lifecycle"], "installedAt": datetime.now(timezone.utc).isoformat(), "suiteRoot": str(suite), "managedFiles": future_managed, "suiteSha256": suite_digest(suite)}
        if audit["legacyArtifactReview"] is not None:
            future_manifest["legacyArtifactReview"] = audit["legacyArtifactReview"]
        manifest_protocol(future_manifest)
        planned[str(MANIFEST_RELATIVE)] = json.dumps(future_manifest, ensure_ascii=False, indent=2) + "\n"
        record = {"projectRoot": str(project), "status": "applying", "before": before, "planned": planned, "createdAt": datetime.now(timezone.utc).isoformat(), "offlineWritersStoppedSource": offline_ack.strip(), "taskDataMigration": "not-performed", "legacyArtifactReview": audit["legacyArtifactReview"]}
        atomic_json(transaction / "transaction.json", record)
        refreshed_states = installation_states(project, audit["legacyArtifactReview"])
        planned_schemas = runtime_state_schemas(planned.get('.codex-rules/_shared/task_runtime.py'))
        if any(state.get('pendingExecutions') or state.get('taskProtocolVersion') == TASK_PROTOCOL and state['status'] not in {'closed', 'cancelled'} or state['schemaVersion'] != '1.0' and state['schemaVersion'] not in planned_schemas for state in refreshed_states):
            raise ValueError('首写前任务所有权/执行/格式变化；目标运行器不支持，未替换受管文件')
        for name, text in planned.items():
            atomic_text(safe_project_path(project, name), text)
        record["after"] = {name: project_text(project, name) for name in paths}
        record["status"] = "complete"
        atomic_json(transaction / "transaction.json", record)
        return {"status": "updated", "transaction": str(transaction), "version": suite_version(suite)}


def runtime_state_schemas(text: str | None) -> set[str]:
    """只解析目标运行器的静态格式声明；旧协议2无声明时仅支持2.0，不执行备份代码。"""
    if text is None:
        return set()
    declarations = [node.value for node in ast.parse(text).body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'SUPPORTED_STATE_SCHEMAS' for target in node.targets)]
    if not declarations:
        return {'2.0'}
    if len(declarations) != 1:
        raise ValueError('目标运行器状态格式声明重复')
    value = ast.literal_eval(declarations[0])
    if not isinstance(value, (tuple, list)) or not value or any(not isinstance(item, str) for item in value):
        raise ValueError('目标运行器状态格式声明无效')
    return set(value)


def restore(project: Path, transaction: Path, offline_ack: str = "", artifact_review: dict | None = None) -> dict:
    """恢复指定升级事务；恢复前逐项核对当前内容，遇到后续本地修改拒绝覆盖。"""
    if not transaction.resolve().is_relative_to((project / ".ai-project/upgrades").resolve()):
        raise ValueError("恢复记录必须属于当前项目 upgrades 目录")
    require_offline(offline_ack)
    with installation_locks(project):
        record = ReadBoundary(project).read_json((transaction / "transaction.json").relative_to(project).as_posix())
        if record["projectRoot"] != str(project):
            raise ValueError("升级事务属于其他项目")
        # 安装可能尚未写新清单；只信这次事务原先保存的精确审查，不从当前 manifest 偷换范围。
        review = record.get("legacyArtifactReview")
        if artifact_review is not None and artifact_review != review:
            raise ValueError("恢复提供的材料审查不属于原安装事务")
        states = installation_states(project, review)
        if any(state.get("pendingExecutions") for state in states):
            raise ValueError("任务仍有活跃或遗留验证执行；先核实并显式 release-execution，停写声明不能越过安装恢复门禁")
        previous_text = record["before"].get(str(MANIFEST_RELATIVE))
        previous_protocol = manifest_protocol(json.loads(previous_text)) if previous_text else "legacy"
        if any(state.get("taskProtocolVersion") == TASK_PROTOCOL and (previous_protocol != TASK_PROTOCOL or state["status"] not in {"closed", "cancelled"}) for state in states):
            raise ValueError("安装恢复不能降级新协议任务或覆盖活动任务；任务数据回滚不属于安装 restore")
        supported = runtime_state_schemas(record['before'].get('.codex-rules/_shared/task_runtime.py'))
        if any(state.get('schemaVersion') == '2.1' and '2.1' not in supported for state in states):
            raise ValueError('旧运行器不支持2.1连续元数据确认；即使任务已关闭，安装 restore 也不能降级任务语义')
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
        record["restoreOfflineWritersStoppedSource"] = offline_ack.strip()
        atomic_json(transaction / "transaction.json", record)
        return {"status": "restored", "transaction": str(transaction)}


def main() -> int:
    """分派审计、预览、升级或恢复；实际更新有独立锁和备份。"""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    artifacts = sub.add_parser("review-artifacts")
    artifacts.add_argument("project", type=Path)
    artifacts.add_argument("--directory", action="append", required=True)
    setup_check = sub.add_parser("setup-lock-check")
    setup_check.add_argument("project", type=Path)
    setup_check.add_argument("--suite", type=Path, required=True)
    setup = sub.add_parser("setup-new")
    setup.add_argument("project", type=Path)
    setup.add_argument("--suite", type=Path, required=True)
    setup.add_argument("--setup-argv", nargs=argparse.REMAINDER)
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
    update.add_argument("--offline-writers-stopped", default="")
    adopt = sub.add_parser("adopt")
    adopt.add_argument("project", type=Path)
    adopt.add_argument("--suite", type=Path, required=True)
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("project", type=Path)
    restore_parser.add_argument("--suite", type=Path, required=True)
    restore_parser.add_argument("--transaction", type=Path, required=True)
    restore_parser.add_argument("--offline-writers-stopped", default="")
    for command_parser in (audit, update, restore_parser):
        command_parser.add_argument("--legacy-artifact-review", type=Path)
        command_parser.add_argument("--legacy-artifact-review-sha256")
    args = parser.parse_args()
    project = args.project.expanduser().resolve()
    if args.command == "review-artifacts":
        print(json.dumps(review_artifacts(project, args.directory), ensure_ascii=False, indent=2))
        return 0
    suite = args.suite.expanduser().resolve()
    if args.command == "setup-lock-check":
        if not os.environ.get(INSTALL_LOCK_ENV):
            raise ValueError("首装子进程缺少继承双锁")
        with installation_locks(project):
            require_fresh_installation(project)
        return 0
    if args.command == "setup-new":
        print(json.dumps(setup_new(project, suite, args.setup_argv), ensure_ascii=False, indent=2))
        return 0
    if args.command == "restore":
        with installation_locks(project):
            review = load_artifact_review(project, args.legacy_artifact_review, args.legacy_artifact_review_sha256)
            result = restore(project, args.transaction.resolve(), args.offline_writers_stopped, review)
        print(json.dumps(result, ensure_ascii=False, indent=2))
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
        result = record_manifest(project, suite, framework, lifecycle.get("state", "unknown"), mappings, legacy=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "update" and not args.dry_run:
        # 文件 SHA 也在实际双锁内重读；预览之后换控制文件不能偷换已批准审查。
        with installation_locks(project):
            review = load_artifact_review(project, args.legacy_artifact_review, args.legacy_artifact_review_sha256)
            result = upgrade(project, suite, args.offline_writers_stopped, review)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    review = load_artifact_review(project, args.legacy_artifact_review, args.legacy_artifact_review_sha256)
    result = audit_project(project, suite, review)
    if args.command == "audit":
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["safeToUpdate"] else 2
    if not result["safeToUpdate"]:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("Refusing unsafe update; inspect managed-file drift, active protocol-2 tasks, and pending executions in the audit.", file=sys.stderr)
        return 3
    if args.dry_run:
        print(json.dumps({"audit": result, "applyMode": "managed-files-only", "recovery": "python3 installers/project_framework.py restore <project> --suite <suite> --transaction <upgrade-directory>"}, ensure_ascii=False, indent=2))
        return 0
    raise ValueError("未知安装命令")


if __name__ == "__main__":
    raise SystemExit(main())
