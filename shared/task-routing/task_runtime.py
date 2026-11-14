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
import unicodedata
import stat
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path

from read_boundary import ReadBoundary, BoundaryError, SNAPSHOT_EXCLUDES, require_complete, task_read_scope, bootstrap_bytes, secure_fd, protected_name, task_only_current, task_only_scope, external_control_bytes, task_metadata_scope, canonical_relative

# 仅跳过生成缓存和任务自身状态；业务源码、规则和第三方目录仍参与差异检查。
IGNORED_DIRS = {".git", ".ai-project", ".migration-workspace-v2", "DerivedData", "build", "node_modules", "__pycache__", ".aws", ".ssh", ".gnupg"}
# 不读取凭据内容；这类文件始终不应成为任务允许编辑范围。
SECRET_SUFFIXES = {".p12", ".pem", ".key", ".mobileprovision", ".cer"}
# 默认禁止修改的依赖目录；项目可追加公共库路径，不能削弱这些默认值。
PROTECTED = (".git/**", ".aws/**", ".ssh/**", ".gnupg/**", "Pods/**", "**/Pods/**", "Carthage/**", "**/Carthage/**", "vendor/**", "**/vendor/**", "oh_modules/**", "**/oh_modules/**", ".build/**", "**/*.xcframework/**", "**/*.framework/**")
# 完成维度互相独立；提交与推送不能由编译成功推导。
TASK_PROTOCOL = "2"  # 协议版本要求安装器与所有持久任务入口一致；旧进程必须离线停写。
SUPPORTED_STATE_SCHEMAS = ("2.0", "2.1")  # 2.1 元数据确认链必须让不认识其语义的旧 writer/installer 失败关闭。
TERMINAL = {"closed", "cancelled"}  # 释放未来写所有权；不推导任何验收维度通过。
STATUSES = {"active", "waiting-permission", "blocked"} | TERMINAL

DIMENSIONS = ("configuration", "codeModified", "compiled", "automatedTests", "realDevice", "submitted", "pushed")


# 安装清单完整性契约；安装器与运行时唯一共用来源，新增受管文件须纳入这里及实际复制链。
RUNTIME_MANAGED_MAPPINGS = {
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
        (".codex-rules/_shared/task_router.py", "shared/task-routing/task_router.py"),
        (".codex-rules/_shared/framework_router.py", "shared/task-routing/framework_router.py"),
        (".codex-rules/_shared/task_runtime.py", "shared/task-routing/task_runtime.py"),
        (".codex-rules/_shared/read_boundary.py", "shared/task-routing/read_boundary.py"),
        (".codex-rules/_shared/references/task-control.md", "shared/context-engineering/task-control.md"),
        (".codex-rules/_shared/references/read-boundary.md", "shared/context-engineering/read-boundary.md"),
        (".migration-v2/references/code-quality-contract.md", "shared/code-quality/code-quality-contract.md"),
    ],
}


def task_scoped(function):
    """每次调用按显式任务 ID 装载临时范围，结束后恢复上下文，避免跨线程/任务污染。"""
    @wraps(function)
    def scoped(root, task=None, *args, **kwargs):
        """保留原接口；check_scope 的第二参数是含 taskId 的状态。"""
        original_root = root.expanduser()
        root = original_root.resolve()
        if function.__name__ == 'run_verification':
            # 仅将明确指定且解析到同一项目根的前缀映射到规范根；根内路径继续逐段审查。
            if len(args) >= 4:
                values = list(args)
                values[3] = canonical_project_path(original_root, root, values[3])
                args = tuple(values)
            elif 'report_path' in kwargs:
                kwargs['report_path'] = canonical_project_path(original_root, root, kwargs['report_path'])
        if task is None:
            task = kwargs.pop('task_id', None) if 'task_id' in kwargs else kwargs.pop('state', None)
        task_id = task['taskId'] if isinstance(task, dict) else task
        only = task_only_current(root)
        if only and task_id != only[1]:
            raise BoundaryError('任务专用范围不能用于其他任务')
        if only and function.__name__ == 'resume':
            with task_metadata_scope(root, task_id), task_read_scope(root, task_id):
                protocol_gate(root)
                historical = task_record(root, task_dir(root, task_id) / 'state.json', allow_legacy=True)
                if historical['status'] in TERMINAL:
                    return {**historical, 'historical': True}
        if only and function.__name__ == 'lifecycle_transition' and (args[0] if args else kwargs.get('action')) == 'complete-code':
            # 已交付任务仅可按原操作重放；先走自有元数据通道，不恢复旧业务读取授权。
            with task_metadata_scope(root, task_id), task_read_scope(root, task_id):
                protocol_gate(root)
                historical = task_record(root, task_dir(root, task_id) / 'state.json', allow_legacy=True)
                if historical['status'] in TERMINAL:
                    return function(root, task, *args, **kwargs)
        metadata = task_metadata_scope(root, task_id) if function.__name__ == 'lifecycle_transition' and (args[0] if args else kwargs.get('action')) in {'cancel', 'release-execution'} and task_only_current(root) else contextlib.nullcontext()
        with metadata, task_read_scope(root, task_id, kwargs.get('read_policy')):
            protocol_gate(root)
            state_path = task_dir(root, task_id) / 'state.json'
            if ReadBoundary(root).exists(state_path.relative_to(root.resolve()).as_posix()):
                task_record(root, state_path, allow_legacy=function.__name__ in {'check_scope', 'lifecycle_transition', 'resume'})
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
    """同目录原子替换并同步父目录；replace 前失败保留旧文件，之后失败须读取核对提交。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    # 同文件系统的替换防止断线留下半份 JSON；并发一致性另由 lock 保证。
    fd, temporary = tempfile.mkstemp(prefix=".ai-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fsync_directory(path: Path) -> None:
    """持久化已替换目录项；失败暴露为不确定提交，调用者须读 state 后按操作 ID 重放。"""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def protocol_gate(root: Path) -> None:
    """拒绝部分安装/未知协议；无安装清单的合成工程仍可用，不能防止未停写的旧进程。"""
    boundary = ReadBoundary(root)
    name = '.ai-project/framework-installation.json'
    if boundary.exists(name):
        manifest = boundary.read_json(name)
        if manifest.get('taskProtocolVersion') != TASK_PROTOCOL:
            raise ValueError('安装协议旧版或未知；先离线审计升级，禁止混合运行')
        rules_identity(root)

    elif not task_only_current(root):
        # 首装中断/旧目录不能因缺清单被当作未安装。只探测已授权入口，不扩大白名单。
        for folder in ('.codex-rules', '.migration-v2'):
            if boundary.allowed(folder):
                try:
                    fd = secure_fd(root, folder, directory=True)
                except FileNotFoundError:
                    continue
                else:
                    os.close(fd)
                    raise ValueError('存在规则目录但缺安装清单；部分安装或旧安装须离线审计')
            for relative in (folder + '/_shared/task_runtime.py', folder + '/project.json'):
                if boundary.allowed(relative) and boundary.exists(relative):
                    raise ValueError('存在规则入口但缺安装清单；不能绕过安装协议')


def validate_metadata_history(state: dict) -> None:
    """仅在内存核验2.1完整逐路径摘要链；不读取文件、不补造历史或认证人工授权。"""
    history, latest = state.get('metadataAcknowledgements'), state.get('acknowledgedMetadata')
    if state.get('metadataAckProtocol') != '2' or not isinstance(history, list) or not history or not isinstance(latest, dict):
        raise ValueError('连续元数据确认协议或历史不完整')
    tails = {}
    for entry in history:
        if not isinstance(entry, dict) or entry.get('taskId') != state['taskId'] or not isinstance(entry.get('path'), str):
            raise ValueError('元数据历史记录归属不完整')
        name = entry['path']
        if canonical_relative(name) != name or not re.fullmatch(r'.+\.xcworkspace/xcuserdata/[^/]+\.xcuserdatad/UserInterfaceState\.xcuserstate', name):
            raise ValueError('元数据历史不是精确 UI 状态路径')
        origin = state['baseline'].get(name, 'absent')
        expected = tails[name]['newSha256'] if name in tails else origin
        if entry.get('baselineSha256', entry.get('oldSha256')) != origin or entry.get('oldSha256') != expected or not isinstance(entry.get('newSha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', entry['newSha256']) or entry['newSha256'] == expected:
            raise ValueError('元数据确认历史摘要断链或原始基线不符')
        if any(not isinstance(entry.get(key), str) or not entry[key].strip() for key in ('reason', 'confirmedBy', 'authorizationSource', 'at')) or datetime.fromisoformat(entry['at'].replace('Z', '+00:00')).tzinfo is None:
            raise ValueError('元数据确认历史缺少授权来源或带时区时间')
        tails[name] = entry
    if tails != latest:
        raise ValueError('元数据确认历史末轮与当前确认记录不一致')


def validate_delivery(state: dict) -> None:
    """代码交付是完整、可追溯的范围声明；部分字段不能成为继承旧修改的授权来源。"""
    delivery = state['codeDelivery']
    if not isinstance(delivery, dict) or delivery.get('schemaVersion') != '1.0' or delivery.get('kind') != 'code-complete-awaiting-acceptance' or state['status'] != 'closed' or type(delivery.get('generation')) is not int or not 1 <= delivery['generation'] <= state.get('generation', 0):
        raise ValueError('代码交付格式或生命周期不完整')
    if not isinstance(delivery.get('operationId'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', delivery['operationId']) or any(not isinstance(delivery.get(key), str) or not delivery[key].strip() for key in ('reason', 'confirmedBy', 'authorizationSource', 'at')) or datetime.fromisoformat(delivery['at'].replace('Z', '+00:00')).tzinfo is None:
        raise ValueError('代码交付缺少完整操作/授权/时间记录')
    if delivery.get('snapshot') != {name: state['checkpoint'].get(name) for name in state['allowedFiles']} or not isinstance(delivery.get('rulesIdentity'), dict) or not delivery['rulesIdentity']:
        raise ValueError('代码交付范围/摘要/规则不完整')
    history = [entry for entry in state.get('lifecycleHistory', []) if isinstance(entry, dict) and entry.get('operationId') == delivery['operationId']]
    if len(history) != 1 or history[0].get('request', {}).get('action') != 'complete-code' or history[0].get('result', {}).get('status') != 'code-delivered' or history[0]['result'].get('generation') != delivery['generation']:
        raise ValueError('代码交付与生命周期原子记录不一致')
    request = history[0]['request']
    if delivery['at'] != history[0].get('at') or any(delivery[key] != request.get(key) for key in ('reason', 'confirmedBy', 'authorizationSource')):
        raise ValueError('代码交付授权来源与原操作不一致')


def validate_state(state: dict, allow_legacy: bool = False) -> dict:
    """未知/部分任务格式失败关闭；旧记录仍参与冲突，但须显式迁移才能继续。"""
    required = {'taskId', 'identity', 'baseRevision', 'allowedFiles', 'baseline', 'checkpoint', 'status', 'completion', 'readCoverage', 'owner', 'request', 'completed', 'todo', 'blockers', 'nextAction', 'updatedAt'}
    if not isinstance(state, dict) or not required.issubset(state) or state['status'] not in STATUSES:
        raise ValueError('任务状态格式不完整或状态未知')
    if any(not isinstance(state[key], str) for key in ('owner', 'request', 'nextAction', 'updatedAt')) or any(not isinstance(state[key], list) for key in ('completed', 'todo', 'blockers')):
        raise ValueError('任务交接字段缺失或类型错误')
    if not isinstance(state['taskId'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', state['taskId']):
        raise ValueError('任务 ID 无效')
    if 'pendingExecutions' in state and (not isinstance(state['pendingExecutions'], dict) or any(not isinstance(key, str) or not isinstance(value, dict) for key, value in state['pendingExecutions'].items())):
        raise ValueError('执行租约格式未知')
    if not isinstance(state['allowedFiles'], list) or not state['allowedFiles'] or any(not isinstance(name, str) or canonical_relative(name) != name for name in state['allowedFiles']) or len(set(state['allowedFiles'])) != len(state['allowedFiles']):
        raise ValueError('任务编辑文件列表无效')
    if not all(isinstance(state[key], dict) for key in ('identity', 'baseline', 'checkpoint', 'completion', 'readCoverage')) or not isinstance(state['baseRevision'], str):
        raise ValueError('任务嵌套字段类型错误')
    if not {'projectRoot', 'repositoryRoot', 'repositoryId', 'platform', 'fields', 'configSha256', 'readCoverage'}.issubset(state['identity']) or state['identity']['platform'] not in {'ios', 'harmonyos'} or not isinstance(state['identity']['fields'], dict):
        raise ValueError('任务原始身份不完整')
    if state['readCoverage'].get('status') not in {'complete', 'partial'} or not set(DIMENSIONS).issubset(state['completion']) or not isinstance(state.get('protectedGlobs'), list) or any(not isinstance(value, str) for value in state.get('protectedGlobs', [])):
        raise ValueError('任务范围/完成维度/保护字段不完整')
    if any(not isinstance(item, (str, dict)) or isinstance(item, dict) and (not isinstance(item.get('status'), str) or not isinstance(item.get('binding'), dict)) for item in state['completion'].values()):
        raise ValueError('完成维度不是已知状态或完整证据绑定')
    for key in ('baseline', 'checkpoint'):
        if any(not isinstance(name, str) or not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value) for name, value in state[key].items()):
            raise ValueError('任务快照不是有效路径摘要映射')
    if state.get('schemaVersion') == '1.0' and 'taskProtocolVersion' not in state:
        if not allow_legacy and state['status'] not in TERMINAL:
            raise ValueError('旧任务须显式 migrate-task continue/cancel；禁止自动换身份')
    elif state.get('schemaVersion') not in SUPPORTED_STATE_SCHEMAS or state.get('taskProtocolVersion') != TASK_PROTOCOL or type(state.get('generation')) is not int or state['generation'] < 1 or not all(isinstance(state.get(key), dict) for key in ('stableIdentity', 'acceptedContext')) or not isinstance(state.get('acceptedRevision'), str) or not {'projectRoot', 'device', 'inode', 'repositoryRoot', 'repositoryId', 'platform', 'target'}.issubset(state['stableIdentity']) or not {'projectRoot', 'repositoryRoot', 'repositoryId', 'platform', 'fields', 'configSha256', 'readCoverage'}.issubset(state['acceptedContext']):
        raise ValueError('任务协议未知或部分升级，禁止写入')
    if state.get('schemaVersion') == '2.1':
        validate_metadata_history(state)
    elif 'metadataAckProtocol' in state or 'metadataAcknowledgements' in state:
        raise ValueError('连续元数据确认必须使用明确2.1状态格式，不能降级伪装旧记录')
    if 'codeDelivery' in state:
        validate_delivery(state)
    if 'predecessorDelivery' in state:
        predecessor = state['predecessorDelivery']
        if not isinstance(predecessor, dict) or not isinstance(predecessor.get('taskId'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', predecessor['taskId']) or any(not isinstance(predecessor.get(key), str) or not predecessor[key].strip() for key in ('confirmedBy', 'authorizationSource')) or any(not isinstance(predecessor.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', predecessor[key]) for key in ('stateSha256', 'deliverySha256')) or predecessor.get('snapshot') != {name: state['baseline'].get(name) for name in state['allowedFiles']}:
            raise ValueError('关联交付的来源、授权或初始摘要不完整')
    return state


def task_record(root: Path, path: Path, allow_legacy: bool = False) -> dict:
    """读取时绑定真实目录 ID/项目根；损坏记录不能串到另一个任务或从冲突集合消失。"""
    state = validate_state(read_json(path), allow_legacy=allow_legacy)
    if path.parent.name != state['taskId'] or state['identity']['projectRoot'] != str(root.resolve()):
        raise ValueError('任务记录与目录 ID/项目根不一致')
    return state


def root_anchor(root: Path) -> dict:
    """真实根路径及文件系统对象区分复制工程；根符号别名在入口归一化。"""
    info = os.stat(root.resolve())
    return {'projectRoot': str(root.resolve()), 'device': info.st_dev, 'inode': info.st_ino}


def stable_identity(identity: dict, root: Path) -> dict:
    """稳定身份只含位置、仓库/worktree、平台与选定目标值；配置摘要和分支另作上下文。"""
    return {**root_anchor(root), **{key: identity[key] for key in ('repositoryRoot', 'repositoryId', 'platform')},
            'target': {key: field.get('value') for key, field in identity['fields'].items()}}


def current_revision(root: Path) -> str:
    """完整授权才探测 HEAD；部分模式不启动 Git、不扩展读取范围。"""
    return (git(root, 'rev-parse', 'HEAD') or 'not-applicable') if ReadBoundary(root).policy['mode'] == 'complete' else 'unverified-read-scope'


def observe_for_task(root: Path, state: dict) -> dict:
    """按原任务目标重探测；观测字段也固定选择值，不能悄悄切换同名目标。"""
    original = state.get('acceptedContext', state['identity'])
    selection = {key: field['value'] for key, field in original['fields'].items() if field.get('value') is not None}
    current = observe_identity(root, original['platform'], selection)
    for key, field in original['fields'].items():
        if field.get('status') == 'observed' and current['fields'][key].get('value') == field.get('value'):
            # 保留原观测来源标记；旁支目标新增不会把原目标误改成未知或新目标。
            current['fields'][key] = dict(field)
    return current


def task_identity(root: Path, state: dict, validated_rules: dict | None = None) -> dict:
    """核对稳定身份及明确接受的上下文；原 identity/baseline 从不重写。"""
    validate_state(state)
    if validated_rules is None:
        rules_identity(root)
    current = observe_for_task(root, state)
    if stable_identity(current, root) != state['stableIdentity'] or current != state['acceptedContext'] or current_revision(root) != state['acceptedRevision']:
        raise ValueError('任务目标、配置或 revision 漂移；先显式 migrate-task 预览确认')
    return current


def invalidate_completion(state: dict) -> None:
    """承接或生命周期变化只降级旧证据，保留报告与原绑定。"""
    for item in state['completion'].values():
        if isinstance(item, dict):
            item['evidenceValidity'] = 'stale'
            if item.get('status') == 'passed':
                item['status'] = 'stale'


def save_state(directory: Path, state: dict) -> str:
    """state 原子提交为唯一事实源；交接失败可重建，绝不反滚已提交任务。"""
    atomic_json(directory / 'state.json', state)
    try:
        write_handoff(directory, state)
        return 'updated'
    except OSError:
        return 'stale-rebuild-from-state'


def require_verification_idle(root: Path, task_id: str) -> None:
    """只探测已存在的验证锁；不创建预览文件，锁存在且被持有时不能交出范围。"""
    relative = ReadBoundary(root).require(f'.ai-project/tasks/{task_id}/verification.lock')
    try:
        descriptor = secure_fd(root, relative)
    except FileNotFoundError:
        return
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('验证锁仍被其他runner持有；不能释放范围或确认元数据') from error
    finally:
        os.close(descriptor)


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
    return protected_name(path.as_posix())


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
    if task_only_current(root):
        boundary = ReadBoundary(root)
        try:
            fd = secure_fd(root, '.ai-project/tasks', directory=True)
        except FileNotFoundError:
            return []
        try:
            with os.scandir(fd) as entries:
                names = sorted(entry.name for entry in entries)
            result = []
            for name in names:
                if name.startswith('.init-'):
                    continue
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', name):
                    raise BoundaryError('任务元数据存在无法确认的条目')
                relative = f'.ai-project/tasks/{name}/state.json'
                if boundary.exists(relative): result.append(root / relative)
            return result
        finally:
            os.close(fd)
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
    observe_git = boundary.policy['mode'] == 'complete' and boundary.project_policy.get('identityGitMode') != 'unverified'
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
    if manifest.get('taskProtocolVersion') != TASK_PROTOCOL:
        raise ValueError('框架安装协议不兼容；离线升级后逐条迁移，不能混合 writer')
    framework = manifest.get('framework')
    if framework not in RUNTIME_MANAGED_MAPPINGS:
        raise ValueError('未知安装框架协议')
    required = dict(RUNTIME_MANAGED_MAPPINGS[framework])
    managed = manifest.get('managedFiles')
    if not isinstance(managed, list) or any(not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ('path', 'source', 'installedSha256', 'sourceSha256')) for item in managed):
        raise ValueError('安装文件协议格式不完整')
    mappings = {item['path']: item['source'] for item in managed}
    if len(mappings) != len(managed) or any(mappings.get(key) != value for key, value in required.items()):
        raise ValueError('安装清单缺少协议入口或重复路径；禁止混合安装')
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
    """读取项目当前确认上下文；任务另用 task_identity，配置漂移不会自动改写历史身份。"""
    root = root.resolve()
    protocol_gate(root)
    if validated_rules is None:
        rules_identity(root)
    only = task_only_current(root)
    if only:
        boundary = ReadBoundary(root)
        spec = boundary.task_policy
        state_name = f'.ai-project/tasks/{only[1]}/state.json'
        if boundary.exists(state_name):
            expected = boundary.read_json(state_name)['identity']
        else:
            current = observe_identity(root, spec['platform'], spec['identitySelection'])
            if any(field['status'] == 'needs-confirmation' for field in current['fields'].values()):
                raise ValueError('单任务身份未明确确认')
            return current
    else:
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
def begin(root: Path, task_id: str, files: list[str], request: str, owner: str = "user-session", protected: list | None = None, read_policy: dict | None = None, from_delivery: str | None = None, confirmed_by: str = '', authorization_source: str = '') -> dict:
    """开始新任务并保留不可变基线。

    root 为项目根，task_id 为唯一任务 ID，files 为允许编辑的相对文件，request 为需求；
    owner 记录任务归属，protected 只能追加禁改 glob。返回机器状态，写 baseline/state/handoff；
    身份不符、已有用户修改、范围重叠或重复 ID 抛错，不覆盖旧任务。
    """
    # 统一 Mac 目录别名，避免把本任务误认成另一个任务或项目。
    root = root.resolve()
    directory = task_dir(root, task_id)
    files = sorted({ReadBoundary(root).require(name) for name in files})
    only = task_only_current(root)
    if from_delivery and only:
        raise BoundaryError('任务专用范围不能继承另一任务的代码交付；请先单独核对授权范围')
    if only and files != sorted(ReadBoundary(root).task_policy['editableFiles']):
        raise ValueError('任务编辑文件与用户确认的精确范围不一致')
    if not files:
        raise ValueError("持久任务必须声明 --file 编辑范围；范围未知时先只读调查")
    policy_path = root / ".ai-project/protected-paths.json"
    boundary = ReadBoundary(root)
    project_protected = read_json(policy_path).get("patterns", []) if boundary.exists(".ai-project/protected-paths.json") else []
    blocked = list(PROTECTED) + project_protected + list(protected or [])
    for name in files:
        boundary.require(name)
        # 授权后先以 O_NOFOLLOW 检查，再解析；避免链接别名导致私密目标被探测。
        boundary.exists(name)
        safe_path(root, name)
        if match(name, blocked) or secret(Path(name)):
            raise ValueError(f"禁止编辑第三方或凭据: {name}")
    with project_lock(root, ".ai-project/tasks.lock"):
        identity = confirmed_identity(root)  # 取得锁后重核，禁止发布锁前观测的身份。
        if directory.exists():
            raise ValueError("任务 ID 已存在，请 resume，禁止重新建立基线")
        for path in task_state_paths(root):
            if path.parent.name.startswith(".init-"):
                continue
            other = task_record(root, path, allow_legacy=True)
            if set(files).intersection(other['allowedFiles']):
                if other.get('pendingExecutions'):
                    raise ValueError('旧任务验证尚未确认退出；完成或明确停写后 release-execution')
                if other['status'] not in TERMINAL:
                    raise ValueError(f"编辑范围与活动任务冲突: {other['taskId']}")
                verification = f'.ai-project/tasks/{other["taskId"]}/verification.lock'
                if other['readCoverage']['status'] == 'complete' and boundary.exists(verification):
                    # 已取消的外部命令可能仍写源码；进程退出前不能把同文件交给新任务。
                    with project_lock(root, verification):
                        pass
        captured = capture_snapshot(root)
        baseline = captured["paths"]
        predecessor = None
        inherited = set()
        if from_delivery:
            if not confirmed_by.strip() or not authorization_source.strip():
                raise ValueError('继承代码交付须明确确认人和授权来源；释放本身不授权继续修改')
            previous = task_record(root, task_dir(root, from_delivery) / 'state.json')
            delivery = previous.get('codeDelivery')
            if previous['status'] != 'closed' or previous.get('pendingExecutions') or not isinstance(delivery, dict) or delivery.get('schemaVersion') != '1.0' or delivery.get('kind') != 'code-complete-awaiting-acceptance' or delivery.get('generation') != previous['generation'] or delivery.get('snapshot') != {name: previous['checkpoint'].get(name) for name in previous['allowedFiles']}:
                raise ValueError('原任务不是可核对的代码交付，不能继承旧修改')
            task_identity(root, previous)
            if delivery.get('rulesIdentity') != rules_identity(root) or previous['readCoverage'] != captured['coverage'] or not set(files).issubset(previous['allowedFiles']):
                raise ValueError('交付关联不能扩大编辑或读取范围')
            blocked = sorted(set(blocked) | set(previous['protectedGlobs']))
            for name in files:
                if baseline.get(name) != delivery['snapshot'].get(name):
                    raise ValueError('交付后源码摘要变化；禁止继承或重设原冻结基线')
            inherited = set(files)
            predecessor = {'taskId': from_delivery, 'stateSha256': digest(previous), 'deliverySha256': digest(delivery), 'snapshot': {name: baseline.get(name) for name in files}, 'confirmedBy': confirmed_by, 'authorizationSource': authorization_source}
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
        if set(files).intersection(dirty_names) - inherited:
            raise ValueError("允许编辑文件已有用户修改；请先人工确认边界，不能把整文件归入新任务")
        state = {"schemaVersion": "2.0", "taskProtocolVersion": TASK_PROTOCOL, "generation": 1, "stableIdentity": stable_identity(identity, root), "acceptedContext": identity, "acceptedRevision": current_revision(root), "taskId": task_id, "owner": owner, "identity": identity, "baseRevision": (git(root, "rev-parse", "HEAD") or "not-applicable") if boundary.policy["mode"] == "complete" else "unverified-read-scope", "request": request, "allowedFiles": files, "protectedGlobs": blocked, "baseline": baseline, "readCoverage": captured["coverage"], "checkpoint": baseline, "baselineDirtyStatus": dirty_names, "baselineDirtyStatusVerified": boundary.policy["mode"] == "complete", "status": "active", "completed": [], "todo": [], "blockers": [], "nextAction": "实现声明的最小任务范围", "completion": {name: "waiting-user" if name == "realDevice" else "not-run" for name in DIMENSIONS}, "updatedAt": now()}
        if predecessor:
            state['predecessorDelivery'] = predecessor
        # 先完整生成未发布目录，再原子发布；断线不会留下“有 baseline 无 state”的任务。
        directory.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".init-", dir=directory.parent))
        try:
            if only:
                atomic_json(staging / 'task-only.json', ReadBoundary(root).task_policy)
            elif read_policy is not None:
                atomic_json(staging / 'read-policy.json', read_policy)
            atomic_json(staging / "baseline.json", {"taskId": task_id, "identity": identity, "paths": baseline, "coverage": captured["coverage"]})
            atomic_json(staging / "state.json", state)
            write_handoff(staging, state)
            if confirmed_identity(root) != identity or current_revision(root) != state['acceptedRevision']:
                raise ValueError('初始化快照期间身份/revision变化，未发布任务')
            if predecessor:
                refreshed = tree_snapshot(root)
                if digest(task_record(root, task_dir(root, from_delivery) / 'state.json')) != predecessor['stateSha256'] or any(refreshed.get(name) != baseline.get(name) for name in files):
                    raise ValueError('关联交付或源码在初始化期间变化，未发布任务')
            os.replace(staging, directory)
            fsync_directory(directory.parent)
        finally:
            if staging.exists():
                for path in staging.iterdir():
                    path.unlink()
                staging.rmdir()
        return state


@task_scoped
def check_scope(root: Path, state: dict) -> dict:
    """比较当前真实文件与基线；禁止依赖修改、越界和覆盖已有修改，返回差异或抛错。"""
    # 统一 Mac 目录别名，避免把本任务误认成另一个任务或项目。
    root = root.resolve()
    if state.get('readCoverage') != ReadBoundary(root).coverage():
        raise ValueError('项目或任务读取范围已变化，旧基线失效；禁止自动扩大范围恢复')
    current = tree_snapshot(root)
    acknowledged = {**state.get('acknowledgedMetadata', {}), **state.get('acknowledgedContext', {})}
    changed = sorted(name for name in set(current) | set(state["baseline"]) | set(acknowledged) if current.get(name) != state["baseline"].get(name) or name in acknowledged and current.get(name) != acknowledged[name].get('newSha256'))
    # 其他任务的文件允许保持独立变化；本任务只声明自己的改动，保护范围全局检查。
    forbidden = [name for name in changed if match(name, state["protectedGlobs"])]
    if forbidden:
        raise ValueError(f"禁改范围发生变化: {forbidden}")
    # 与本任务并行的合法范围只用于排除，不能据此认领别人的修改。
    other_files = set()  # 只排除仍归其他任务所有、或已关闭且保持检查点内容的文件。
    for path in task_state_paths(root):
        if path.parent.name.startswith(".init-"):
            continue
        other = task_record(root, path, allow_legacy=True)
        if other["taskId"] != state["taskId"]:
            for name in other["allowedFiles"]:
                if other["status"] not in {"closed", "cancelled"} or current.get(name) == other["checkpoint"].get(name):
                    other_files.add(name)
    def acknowledged_change(name: str) -> bool:
        """每轮确认仍锚定原基线；仅当前摘要等于该路径最后一次精确确认才排除。"""
        if name not in acknowledged:
            return False
        entry = acknowledged[name]
        origin = entry.get('baselineSha256', entry.get('oldSha256'))
        original_matches = state['baseline'].get(name) == origin or name not in state['baseline'] and origin == 'absent'
        return original_matches and current.get(name) == entry.get('newSha256')
    unexpected = [name for name in changed if name not in state["allowedFiles"] and name not in other_files and not acknowledged_change(name)]
    if unexpected:
        raise ValueError(f"范围外或原有用户文件发生变化，暂停人工核对: {unexpected}")
    return {"changedFiles": [name for name in changed if name in state["allowedFiles"]], "current": current, "coverage": ReadBoundary(root).coverage()}


def prepare_metadata_ack(root: Path, state: dict, path: str, old_hash: str, new_hash: str, reason: str, confirmed_by: str, authorization_source: str) -> tuple[dict, dict]:
    """逐项准备精确 UI 元数据确认；只复制状态，不承认身份、不检查全局范围或写入。

    首轮 old_hash 必须为原始摘要（新建为 absent），续轮必须为上轮新摘要；
    baselineSha256 始终锚定原基线，历史列表保留每轮记录，不接受删除或跳轮。
    调用者须在同一事务中核身份、范围、状态与预览，再决定是否持久保存 proposed。
    """
    root = root.resolve()
    if not isinstance(path, str) or canonical_relative(path) != path or not re.fullmatch(r'.+\.xcworkspace/xcuserdata/[^/]+\.xcuserdatad/UserInterfaceState\.xcuserstate', path):
        raise ValueError('仅允许规范精确的 Xcode workspace UserInterfaceState 路径')
    if any(not isinstance(value, str) or not value.strip() for value in (reason, confirmed_by, authorization_source)) or not isinstance(old_hash, str) or not isinstance(new_hash, str) or (old_hash != 'absent' and not re.fullmatch(r'[0-9a-f]{64}', old_hash)) or not re.fullmatch(r'[0-9a-f]{64}', new_hash) or old_hash == new_hash:
        raise ValueError('必须提供原因、确认人、授权来源及不同的完整旧/新 SHA256；新建旧值须显式 absent')
    if path in state['allowedFiles'] or match(path, state['protectedGlobs']):
        raise ValueError('不能确认业务编辑范围或受保护路径')
    directory = task_dir(root, state['taskId'])
    # 元数据确认不是抢占另一任务的写所有权；其他不同文件的合法owner仍由check_scope处理。
    for state_path in task_state_paths(root):
        if state_path.parent == directory or state_path.parent.name.startswith('.init-'):
            continue
        other = task_record(root, state_path, allow_legacy=True)
        if (other['status'] not in TERMINAL or other.get('pendingExecutions')) and path in other['allowedFiles']:
            raise ValueError('精确元数据路径仍被另一任务或遗留执行占用，不能确认接管')
    original = read_json(directory / 'baseline.json')
    if original.get('taskId') != state['taskId'] or original.get('paths') != state['baseline'] or original.get('identity') != state['identity']:
        raise ValueError('原始基线与任务状态不一致')
    # 基线锚点与上一轮值分开，后续确认不能把新值偷偷写成原始基线。
    baseline_hash = original['paths'][path] if path in original['paths'] else 'absent'
    acknowledged = state.get('acknowledgedMetadata', {})
    if not isinstance(acknowledged, dict):
        raise ValueError('元数据精确确认不是路径记录映射')
    previous = acknowledged.get(path)
    history = state.get('metadataAcknowledgements', [])
    if not isinstance(history, list) or any(not isinstance(item, dict) or not isinstance(item.get('path'), str) or item.get('taskId') != state['taskId'] for item in history):
        raise ValueError('元数据确认历史不是完整的本任务路径记录列表')
    # 旧版只有map时原样补齐所有旧轮；格式校验复用读状态时的同一个纯函数。
    proposed = json.loads(json.dumps(state))
    represented = {item['path'] for item in history}
    for known_path, latest in acknowledged.items():
        if known_path not in represented:
            proposed.setdefault('metadataAcknowledgements', []).append(json.loads(json.dumps(latest)))
    proposed.update(schemaVersion='2.1', metadataAckProtocol='2')
    if acknowledged or history:
        validate_metadata_history(proposed)
    if previous is None:
        if old_hash != baseline_hash:
            raise ValueError('首次旧摘要或 absent 声明与不可变基线不一致')
    elif previous['newSha256'] != old_hash:
        raise ValueError('续轮旧摘要必须匹配上一轮新摘要')
    actual = hashlib.sha256(ReadBoundary(root).read_bytes(path)).hexdigest()
    if actual != new_hash:
        raise ValueError('当前文件摘要与确认值不一致；不能以 absent 确认删除')
    entry = {'oldSha256': old_hash, 'baselineSha256': baseline_hash, 'newSha256': new_hash, 'reason': reason, 'confirmedBy': confirmed_by, 'authorizationSource': authorization_source, 'taskId': state['taskId'], 'path': path, 'at': now()}
    proposed.setdefault('metadataAcknowledgements', []).append(entry)
    proposed.setdefault('acknowledgedMetadata', {})[path] = entry
    validate_metadata_history(proposed)
    return proposed, entry


@task_scoped
def acknowledge_metadata(root: Path, task_id: str, path: str, old_hash: str, new_hash: str, reason: str, apply: bool = False, preview_hash: str | None = None, confirmed_by: str = '', authorization_source: str = '') -> dict:
    """确认精确 Xcode UI 元数据；保留原始基线，拒绝任何额外越界变化。"""
    root = root.resolve()
    directory = task_dir(root, task_id)
    with project_lock(root, '.ai-project/tasks.lock') if apply else contextlib.nullcontext():
        state = task_record(root, directory / 'state.json')
        if state.get('pendingExecutions'):
            raise ValueError('仍有活跃或遗留验证执行；不能确认元数据或清除执行租约')
        # 旧writer可能尚未登记lease；只探测已存在的锁，不能为只读预览创建占用文件。
        require_verification_idle(root, task_id)
        if state['status'] in {'closed', 'cancelled'} or task_identity(root, state) != state.get('acceptedContext', state['identity']):
            raise ValueError('任务已结束或项目身份变化')
        proposed, entry = prepare_metadata_ack(root, state, path, old_hash, new_hash, reason, confirmed_by, authorization_source)
        scoped = check_scope(root, proposed)
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
        # 原始 state 仍须一致；advisory lock 不能阻止不遵守协议的旧 writer 或外部工具。
        refreshed_state = task_record(root, directory / 'state.json')
        if digest(refreshed_state) != digest(state):
            raise ValueError('备份期间任务状态变化；需要重新预览')
        task_identity(root, refreshed_state)
        refreshed = check_scope(root, proposed)
        if digest(refreshed['current']) != digest(scoped['current']):
            raise ValueError('预览后实际快照变化；需要重新审查')
        proposed['generation'] += 1
        proposed['updatedAt'] = entry['at']
        invalidate_completion(proposed)
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
    state = task_record(root, task_dir(root, task_id) / "state.json", allow_legacy=True)
    validate_state(state, allow_legacy=True)
    if state['status'] in TERMINAL:
        return {**state, 'historical': True}  # 读取终态不验证/激活旧代码，不复用通过证据。
    task_identity(root, state)
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
        state = task_record(root, directory / "state.json")
        if state["status"] in {"closed", "cancelled"}:
            raise ValueError("终结任务不能通过 checkpoint 重开；请用新任务 ID 核对新范围")
        for path in task_state_paths(root):
            if path.parent.name.startswith(".init-") or path.parent == directory:
                continue
            other = task_record(root, path, allow_legacy=True)
            if other["status"] not in {"closed", "cancelled"} and set(other["allowedFiles"]).intersection(state["allowedFiles"]):
                raise ValueError("任务编辑所有权冲突，暂停保存检查点")
        task_identity(root, state)
        scoped = check_scope(root, state)
        for item in state["completion"].values():
            if isinstance(item, dict) and item.get("binding") != binding(root, task_id, item["binding"]["profile"], item["binding"].get("sliceId")):
                item["status"] = "stale"
        state["generation"] += 1
        invalidate_completion(state)
        if status not in STATUSES:
            raise ValueError("未知任务状态")
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
    state = task_record(root, task_dir(root, task_id) / "state.json", allow_legacy=True)
    # 同一次绑定内复用刚核验的规则；下一绑定（包括执行后）始终重新读取。
    rules = rules_identity(root)
    identity = task_identity(root, state, validated_rules=rules)
    if state["status"] in TERMINAL:
        raise ValueError("终态只有历史证据，不可生成当前通过绑定")
    files = check_scope(root, state)["current"]
    return {"taskProtocolVersion": TASK_PROTOCOL, "generation": state["generation"], "stableIdentitySha256": digest(state["stableIdentity"]), "observedContextSha256": digest(identity), "taskId": task_id, "sliceId": slice_id, "projectRoot": str(root.resolve()), "identitySha256": digest(identity), "revision": (git(root, "rev-parse", "HEAD") or "not-applicable") if ReadBoundary(root).policy["mode"] == "complete" else "unverified-read-scope", "codeSha256": digest(files), "profile": profile, "rulesIdentity": rules, "readCoverage": ReadBoundary(root).coverage(), "verificationContractSha256": digest(read_json(root / ".ai-project/verification-commands.json")) if not task_only_current(root) and ReadBoundary(root).exists(".ai-project/verification-commands.json") else None}


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
    if report.get("coverage") != current["readCoverage"] or report.get("limitations") != ["excluded credentials are not bound; external commands are not sandboxed by ReadBoundary"]:
        raise ValueError("构建或测试报告缺失凭据排除与子进程范围限制")
    commands = report.get("commands", [])
    if not commands or any(not item.get("argv") or item.get("exitCode") != 0 for item in commands):
        raise ValueError("通过报告必须包含实际执行命令及成功退出状态")


def start_execution(root: Path, task_id: str, state: dict) -> str:
    """在 tasks 锁内原子登记执行；父进程崩溃不会自动释放同文件所有权。"""
    if state.get('pendingExecutions'):
        raise ValueError('存在未确认退出的验证；先核对并显式终结/停写恢复')
    execution_id = digest({'task': task_id, 'at': now(), 'pid': os.getpid(), 'generation': state['generation']})
    invalidate_completion(state)
    state.setdefault('pendingExecutions', {})[execution_id] = {'startedAt': now(), 'generation': state['generation'], 'runnerPid': os.getpid()}
    save_state(task_dir(root, task_id), state)
    return execution_id


def finish_execution(state: dict, execution_id: str) -> None:
    """只移除本次已同步退出的执行标识；保留终态和历史 generation。"""
    state.setdefault('pendingExecutions', {}).pop(execution_id, None)
    state.setdefault('executionHistory', []).append({'executionId': execution_id, 'finishedAt': now()})


def _check_existing_path(root: Path, relative: Path, *, file_target: bool) -> None:
    """检查现有节点；缺失的合法后代由原有写入流程创建。不是防并发写入的沙箱。"""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(root, flags)
    try:
        for index, part in enumerate(relative.parts):
            last_file = file_target and index == len(relative.parts) - 1
            if last_file:
                try:
                    info = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    return
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('报告目标必须是普通单硬链接文件')
            else:
                try:
                    child = os.open(part, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    return
                except OSError as error:
                    raise ValueError('报告或证据的现有父路径必须是真实目录') from error
                os.close(descriptor)
                descriptor = child
    finally:
        os.close(descriptor)


def _absolute_path(path: Path) -> Path:
    if '..' in path.parts:
        raise ValueError('路径不能包含父级跳转')
    return path if path.is_absolute() else Path.cwd() / path


def canonical_project_path(project_input: Path, root: Path, path: Path) -> Path:
    """仅映射经解析确认为同一项目的根前缀；不解析根内报告路径。"""
    project_input = _absolute_path(project_input.expanduser())
    path = _absolute_path(path)
    if project_input.resolve(strict=True) != root:
        raise ValueError('项目根别名与已确认项目不一致')
    try:
        return root / path.relative_to(project_input)
    except ValueError:
        return path


def validate_gate_paths(root: Path, evidence: Path, report: Path) -> None:
    """只检查既有目录和路径边界；不执行命令、不读取业务内容。"""
    evidence, report = _absolute_path(evidence), _absolute_path(report)
    workspace = root / '.migration-workspace-v2'
    if not evidence.is_relative_to(workspace / 'evidence'):
        raise ValueError('Gate 日志只能写到迁移工作区 evidence 目录')
    if not report.is_relative_to(workspace / 'reports') or report == workspace / 'reports':
        raise ValueError('Gate 报告只能写到迁移工作区 reports 目录，禁止覆盖业务文件')
    _check_existing_path(root, evidence.relative_to(root), file_target=False)
    _check_existing_path(root, report.relative_to(root), file_target=True)


@contextlib.contextmanager
def verification_session(root: Path, task_id: str, *, gate_paths: tuple[Path, Path] | None = None):
    """迁移 Gate 共用锁序与持久执行租约；崩溃租约必须明确停写后人工解除。"""
    execution = contextlib.ExitStack()
    execution_id = None
    session_state = None
    pre_command_rejected = False
    try:
        with project_lock(root, '.ai-project/tasks.lock'):
            protocol_gate(root)
            execution.enter_context(project_lock(root, f'.ai-project/tasks/{task_id}/verification.lock'))
            state = task_record(root, task_dir(root, task_id) / 'state.json')
            if state['status'] != 'active':
                raise ValueError('非活动任务不能运行验证')
            task_identity(root, state)
            check_scope(root, state)  # 确定性范围错误在登记执行租约前拒绝。
            execution_id = start_execution(root, task_id, state)
        session_state = state
        execution_record = dict(state['pendingExecutions'][execution_id])
        execution_state_sha = digest(state)
        if gate_paths is not None:
            # 此处为框架内置、无外部命令的路径核验；执行上下文尚未 yield 给 Gate。
            try:
                validate_gate_paths(root, gate_paths[0], gate_paths[1])
            except (ValueError, OSError):
                pre_command_rejected = True
                raise
            except BaseException:
                session_state['_retainExecutionLease'] = True
                raise
        try:
            yield state
        except BaseException:
            session_state['_retainExecutionLease'] = True  # 中断不能证明外部写入者已经退出。
            raise
    finally:
        execution.close()
        if execution_id is not None and not session_state.get("_retainExecutionLease", False):
            with project_lock(root, '.ai-project/tasks.lock'):
                state = task_record(root, task_dir(root, task_id) / 'state.json')
                if pre_command_rejected and (digest(state) != execution_state_sha or state.get('pendingExecutions', {}).get(execution_id) != execution_record):
                    raise ValueError('Gate 前置失败后任务或执行记录漂移，保留待确认执行')
                finish_execution(state, execution_id)
                if pre_command_rejected:
                    state['executionHistory'][-1].update({'outcome': 'gate-path-rejected-before-gate', 'executionRecord': execution_record, 'stateSha256Before': execution_state_sha, 'projectAnchor': root_anchor(root), 'taskId': task_id, 'proofKind': 'framework-pre-yield-path-check'})
                save_state(task_dir(root, task_id), state)


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


_REPORT_RESERVED = {'state.json', 'baseline.json', 'handoff.md', 'task-only.json', 'read-policy.json', 'verification.lock', 'configuration.json', 'verification-history', 'metadata-confirmations', 'controlled-writes', 'identity-confirmations', 'metadata-writes'}


def _reserved_node_alias(directory_fd: int, name: str) -> bool:
    """除了规范化名称，核对实际现有节点是否与控制节点同一设备和 inode。"""
    try:
        selected = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    for reserved in _REPORT_RESERVED:
        try:
            info = os.stat(reserved, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if (selected.st_dev, selected.st_ino) == (info.st_dev, info.st_ino):
            return True
    return False


def validate_verification_report_path(root: Path, directory: Path, report_path: Path) -> Path:
    """确定性路径错误在登记执行前拒绝；不创建目录，不跟随链接。"""
    report_path = _absolute_path(report_path)
    try:
        rel = report_path.relative_to(directory)
    except ValueError as error:
        raise ValueError('验证报告只能写到本任务目录') from error
    normalized = {unicodedata.normalize('NFC', name).casefold() for name in _REPORT_RESERVED}
    if not rel.parts or unicodedata.normalize('NFC', rel.parts[0]).casefold() in normalized or any(part.startswith('.') for part in rel.parts):
        raise ValueError('验证报告不能覆盖任务控制文件或历史记录')
    fd = secure_fd(root, directory.relative_to(root).as_posix(), directory=True)
    try:
        if _reserved_node_alias(fd, rel.parts[0]):
            raise ValueError('验证报告不能指向任务控制文件或历史记录的等价节点')
    finally:
        os.close(fd)
    _check_existing_path(root, report_path.relative_to(root), file_target=True)
    return report_path


@task_scoped
def run_verification(root: Path, task_id: str, profile: str, kind: str, argv: list[str], report_path: Path, timeout: int = 900) -> dict:
    """执行明确验证契约；锁序 tasks→verification，执行期间仅持验证锁，终态只保留历史。"""
    root = root.resolve()
    if not argv or kind not in {'compiled', 'automatedTests'}:
        raise ValueError('验证仅支持编译或自动测试命令')
    if task_only_current(root):
        raise BoundaryError('任务专用部分范围不执行外部构建或测试')
    directory = task_dir(root, task_id)
    report_path = validate_verification_report_path(root, directory, report_path)
    require_complete(root)
    contracts = read_json(root / '.ai-project/verification-commands.json').get('commands', [])
    contract = next((item for item in contracts if item.get('confirmed') is True and item.get('argv') == argv and item.get('kind') == kind and profile in item.get('profiles', [])), None)
    if contract is None:
        raise ValueError('命令、验证维度和 profile 尚未明确确认')
    check_operation_hold(root, contract)
    # 只在 tasks 锁内取得 verification，释放验证锁后才再次取得 tasks；没有逆序等待。
    execution = contextlib.ExitStack()
    try:
        with project_lock(root, '.ai-project/tasks.lock'):
            execution.enter_context(project_lock(root, f'.ai-project/tasks/{task_id}/verification.lock'))
            validate_verification_report_path(root, directory, report_path)
            initial = task_record(root, directory / 'state.json')
            if initial['status'] != 'active':
                raise ValueError('等待、阻塞或终结的任务不能自动执行验证')
            before = binding(root, task_id, profile)
            execution_id = start_execution(root, task_id, initial)
            expected_state = digest(initial)
        started = now()
        failure = None
        unconfirmed_exit = False
        try:
            result = subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False, timeout=timeout)
            exit_code = result.returncode
            output_digest = digest({'stdout': result.stdout, 'stderr': result.stderr})
        except (OSError, subprocess.TimeoutExpired) as error:
            exit_code = None
            output_digest = digest(str(error))
            failure = type(error).__name__
            unconfirmed_exit = isinstance(error, subprocess.TimeoutExpired)
        try:
            after = binding(root, task_id, profile)
        except (ValueError, OSError) as error:
            after = None
            failure = str(error)
        status = 'blocked' if failure else 'passed' if exit_code == 0 and before == after else 'failed'
        report = {'schemaVersion': '2.0', 'binding': before, 'startedAt': started, 'finishedAt': now(), 'status': status,
                  'reason': failure, 'kind': kind, 'commands': [{'argv': argv, 'cwd': str(root), 'exitCode': exit_code}],
                  'outputSha256': output_digest, 'coverage': before['readCoverage'],
                  'limitations': ['excluded credentials are not bound; external commands are not sandboxed by ReadBoundary']}
        history_path = directory / 'verification-history' / (started.replace(':', '-') + '.json')
        atomic_json(history_path, report)
        atomic_json(report_path, report)
    finally:
        execution.close()
    with project_lock(root, '.ai-project/tasks.lock'):
        state = task_record(root, directory / 'state.json')
        unchanged = digest(state) == expected_state and state['status'] == 'active'
        if not unconfirmed_exit:
            finish_execution(state, execution_id)
        if not unchanged:
            save_state(directory, state)
            report['stateUpdate'] = 'history-only-task-changed'
            atomic_json(history_path, report)
            atomic_json(report_path, report)
            return report
        state['completion'][kind] = {'status': report['status'], 'report': str(report_path), 'binding': before}
        if report['status'] == 'blocked':
            state['status'] = 'blocked'
            state['blockers'].append({'reason': report['reason'], 'report': str(report_path)})
            state['nextAction'] = '核对执行环境、权限或范围冲突后保存检查点，再重新验证'
        state['updatedAt'] = now()
        save_state(directory, state)
    return report


@task_scoped
def verify_configuration(root: Path, task_id: str, profile: str = "D0") -> dict:
    """仅在进程内核对当前身份/规则/允许文件；不执行命令，部分覆盖保持 partial。"""
    root = root.resolve()
    if profile != "D0":
        raise ValueError("进程内配置检查仅支持 D0，不能替代功能/发布验证")
    directory = task_dir(root, task_id)
    with project_lock(root, ".ai-project/tasks.lock"):
        with project_lock(root, f".ai-project/tasks/{task_id}/verification.lock"):
            state = task_record(root, directory / "state.json")
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
            state["completion"]["configuration"] = {"status": status, "report": str(report_path), "binding": before}
            save_state(directory, state)
            return report


@task_scoped
def lifecycle_transition(root: Path, task_id: str, action: str, operation_id: str, reason: str,
                         confirmed_by: str, authorization_source: str, apply: bool = False,
                         preview_hash: str | None = None, context_files: list[str] | None = None,
                         offline_ack: str = '', metadata_ack: dict | None = None) -> dict:
    """逐条预览/承接/取消；操作 ID 与原状态摘要绑定，取消不代表验收通过。"""
    root = root.resolve()
    if action not in {'cancel', 'continue', 'release-execution', 'complete-code'} or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', operation_id or ''):
        raise ValueError('需明确 action 和安全唯一 operation-id')
    if not all(isinstance(value, str) and value.strip() for value in (reason, confirmed_by, authorization_source)):
        raise ValueError('需原因、确认人及实际授权来源；文本记录不代替授权')
    context_files = sorted(set(context_files or []))
    request = {'action': action, 'operationId': operation_id, 'reason': reason, 'confirmedBy': confirmed_by,
               'authorizationSource': authorization_source, 'contextFiles': context_files, 'offlineWritersStopped': offline_ack}
    if metadata_ack is not None:
        if action != 'continue' or not isinstance(metadata_ack, dict) or set(metadata_ack) != {'path', 'oldSha256', 'newSha256'}:
            raise ValueError('组合元数据确认仅供 continue，必须给出精确路径与旧/新摘要')
        request['metadataAcknowledgement'] = metadata_ack
    directory = task_dir(root, task_id)
    with project_lock(root, '.ai-project/tasks.lock') if apply else contextlib.nullcontext():
        state = task_record(root, directory / 'state.json', allow_legacy=True)
        if state['taskId'] != task_id or state['identity'].get('projectRoot') != str(root):
            raise ValueError('任务属于其他项目或 ID')
        # 幂等结果由同一条权威 state 保存；重放不会追加历史或改写文件。
        prior = next((entry for entry in state.get('lifecycleHistory', []) if entry.get('operationId') == operation_id), None)
        if prior:
            if prior['request'] != request or (apply and preview_hash != prior['previewSha256']):
                raise ValueError('operation-id 已被其他参数或预览使用')
            return {**prior['result'], 'replayed': True}
        if state['status'] in TERMINAL and action != 'release-execution':
            raise ValueError('终结任务只能重放原操作，不能恢复或重新承接')
        if action == 'release-execution' and (state['status'] not in TERMINAL or not state.get('pendingExecutions') or not offline_ack.strip()):
            raise ValueError('仅终态遗留执行可显式停写解除，必须记录真实停写来源')
        legacy = state['schemaVersion'] == '1.0'
        if action == 'complete-code' and (legacy or state['status'] != 'active'):
            raise ValueError('代码交付仅接受明确活动的新协议任务；blocked/waiting 或旧任务不能自动完成')
        if legacy and not offline_ack.strip():
            raise ValueError('旧任务迁移须记录已停止旧 writer 的来源；不能在线混合升级')
        anchor = root_anchor(root)
        if not legacy and any(state['stableIdentity'].get(key) != value for key, value in anchor.items()):
            raise ValueError('真实项目根或文件系统对象变化，不能认领复制记录')
        boundary = ReadBoundary(root)
        # 取消无需配置可读/一致；仓库锚点可核对时仍严格区分 worktree 与仓库。
        expected = state.get('acceptedContext', state['identity'])
        if boundary.policy['mode'] == 'complete' and expected.get('repositoryRoot') not in {'unverified-read-scope', 'not-applicable'}:
            top = git(root, 'rev-parse', '--show-toplevel')
            common = git(root, 'rev-parse', '--git-common-dir')
            if not top or str(Path(top).resolve()) != expected['repositoryRoot'] or digest(str((root / common).resolve())) != expected['repositoryId']:
                raise ValueError('仓库/worktree 归属变化，拒绝任务迁移')
        current = None
        changed_context = []
        captured = None
        metadata_entry = None
        proposed = json.loads(json.dumps(state))
        if action == 'complete-code' or metadata_ack is not None:
            if state.get('pendingExecutions'):
                raise ValueError('存在尚未确认退出的执行；不能交付代码或组合确认元数据')
            require_verification_idle(root, task_id)
        if action == 'continue':
            current = observe_for_task(root, state)
            target = stable_identity(current, root)
            original_target = state.get('stableIdentity', stable_identity(state['identity'], root))
            if target != original_target or any(field['status'] == 'needs-confirmation' for field in current['fields'].values()):
                raise ValueError('项目/目标变化须新任务；不能借配置承接切换目标')
            if state['readCoverage'] != boundary.coverage():
                raise ValueError('读取范围变化不能迁移或自动扩权')
            rules_identity(root)
            names = sorted(set(expected['readCoverage']['paths']) | set(current['readCoverage']['paths']))
            captured = capture_snapshot(root)['paths']
            accepted = state.get('acceptedConfiguration', {name: state['baseline'].get(name) for name in names})
            changed_context = [{'path': name, 'oldSha256': accepted.get(name), 'newSha256': captured.get(name)}
                               for name in names if accepted.get(name) != captured.get(name)]
            if context_files != sorted(item['path'] for item in changed_context):
                raise ValueError('需精确确认全部且仅有变化的身份配置文件')
            for entry in changed_context:
                name = entry['path']
                if match(name, state['protectedGlobs']):
                    raise ValueError('不能承接受保护配置修改')
                # 原 baseline 保留，明确确认只允许该配置的当前摘要，未来再变仍阻塞。
                proposed.setdefault('acknowledgedContext', {})[name] = {'oldSha256': state['baseline'].get(name), 'newSha256': captured.get(name), 'operationId': operation_id}
            if metadata_ack is not None:
                proposed, metadata_entry = prepare_metadata_ack(root, proposed, metadata_ack['path'], metadata_ack['oldSha256'], metadata_ack['newSha256'], reason, confirmed_by, authorization_source)
            if check_scope(root, proposed)['current'] != captured:
                raise ValueError('承接期间实际快照变化；重新核对精确摘要')
            proposed.update(acceptedContext=current, stableIdentity=target, acceptedRevision=current_revision(root),
                            acceptedConfiguration={name: captured.get(name) for name in current['readCoverage']['paths']})
        elif action == 'complete-code':
            if context_files:
                raise ValueError('交付代码不能顺带承接配置；请先单独精确确认')
            task_identity(root, state)
            captured = check_scope(root, state)['current']
            if any(captured.get(name) != state['checkpoint'].get(name) for name in state['allowedFiles']):
                raise ValueError('代码与检查点不符；先审查并 checkpoint，不得静默重设冻结')
        else:
            if context_files:
                raise ValueError('终结/释放执行不承接配置；context-files 必须为空')
            if action == 'release-execution':
                require_verification_idle(root, task_id)
        generation = state.get('generation', 0)
        preview = {'taskId': task_id, 'projectAnchor': anchor, 'stateSha256': digest(state), 'generation': generation,
                   'request': request, 'observedContext': current, 'changedContextFiles': changed_context,
                   'observedRevision': current_revision(root) if captured is not None else None,
                   'snapshotSha256': digest(captured) if captured is not None else None,
                   'writes': [f'.ai-project/tasks/{task_id}/state.json', f'.ai-project/tasks/{task_id}/handoff.md']}
        if metadata_entry is not None:
            preview['metadataAcknowledgement'] = metadata_ack
        if action == 'complete-code':
            preview['codeSnapshot'] = {name: captured.get(name) for name in state['allowedFiles']}
        token = digest(preview)
        if not apply:
            return {'status': 'dry-run', **preview, 'previewSha256': token}
        if not preview_hash or preview_hash != token:
            raise ValueError('预览后任务/上下文/文件/参数漂移；重新只读核对')
        if captured is not None:
            if digest(task_record(root, directory / 'state.json', allow_legacy=True)) != digest(state) or tree_snapshot(root) != captured or current_revision(root) != preview['observedRevision']:
                raise ValueError('提交前状态/源码/revision变化，未应用交付或确认')
            if action == 'continue' and observe_for_task(root, state) != current:
                raise ValueError('提交前配置再次变化，未应用承接')
        if legacy:
            proposed['legacyState'] = state  # 单条原子状态保存完整旧记录，不批量替换旧身份。
            proposed.update(schemaVersion='2.0', taskProtocolVersion=TASK_PROTOCOL,
                            stableIdentity=stable_identity(state['identity'], root),
                            acceptedContext=state['identity'], acceptedRevision=state['baseRevision'])
            if action == 'continue':
                proposed.update(stableIdentity=target, acceptedContext=current, acceptedRevision=preview['observedRevision'])
        if metadata_entry is not None:
            proposed.update(schemaVersion='2.1', metadataAckProtocol='2')
        proposed['generation'] = generation + 1
        invalidate_completion(proposed)
        proposed['updatedAt'] = now()
        if action == 'release-execution':
            proposed.setdefault('releasedExecutions', []).append({'executions': proposed.pop('pendingExecutions'), 'operationId': operation_id, 'offlineWritersStopped': offline_ack})
        if action == 'cancel':
            proposed['status'] = 'cancelled'
            proposed['nextAction'] = '已取消；未完成项需新任务显式声明范围，历史证据保留'
        if action == 'complete-code':
            proposed['status'] = 'closed'
            proposed['codeDelivery'] = {'schemaVersion': '1.0', 'kind': 'code-complete-awaiting-acceptance', 'generation': proposed['generation'], 'operationId': operation_id, 'snapshot': preview['codeSnapshot'], 'rulesIdentity': rules_identity(root), 'reason': reason, 'confirmedBy': confirmed_by, 'authorizationSource': authorization_source, 'at': proposed['updatedAt']}
            proposed['nextAction'] = '编辑范围已释放；验收维度和待办保留，后续修改须新任务重新取得精确范围'
        # continue 保留 waiting-permission/blocked；确认配置不代替恢复执行或解除权限暂停。
        result = {'status': 'code-delivered' if action == 'complete-code' else 'cancelled' if action == 'cancel' else 'execution-release-confirmed' if action == 'release-execution' else 'context-confirmed', 'taskId': task_id,
                  'operationId': operation_id, 'previewSha256': token, 'generation': proposed['generation'],
                  'stateSha256Before': digest(state), 'writes': preview['writes']}
        proposed.setdefault('lifecycleHistory', []).append({'operationId': operation_id, 'request': request,
                         'stateSha256Before': digest(state), 'beforeContext': expected,
                         'previewSha256': token, 'result': result, 'at': proposed['updatedAt']})
        if metadata_entry is not None:
            proposed['lifecycleHistory'][-1]['metadataAcknowledgement'] = metadata_entry
        handoff = save_state(directory, proposed)
        return {**result, 'handoff': handoff}


def main() -> int:
    """提供身份/init/resume/checkpoint/verify 命令；预期冲突以 blocked 和非零退出表达。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("identity", "init", "resume", "check", "checkpoint", "verify", "verify-config", "ack-metadata", "migrate-task", "cancel", "release-execution", "complete-code"))
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--task-id")
    parser.add_argument("--task-only", action="store_true", help="使用已发布的任务专用引导范围")
    parser.add_argument("--task-only-spec", type=Path, help="仅 init：用户授权的任务专用引导 JSON")
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
    parser.add_argument('--operation-id', default='')
    parser.add_argument('--action', choices=('continue', 'cancel', 'release-execution'), default='continue')
    parser.add_argument('--offline-writers-stopped', default='')
    parser.add_argument('--metadata-file', help='仅 continue：与精确配置承接同一事务确认的单个 UI 状态文件')
    parser.add_argument('--from-delivery', help='仅 init：显式继承已核代码交付的精确子集，仍须确认人及授权来源')
    args = parser.parse_args()
    root = args.project.expanduser().resolve()
    try:
        if args.metadata_file and (args.command != 'migrate-task' or args.action != 'continue'):
            raise ValueError('--metadata-file 只供 migrate-task continue 的显式组合确认')
        if args.from_delivery and args.command != 'init':
            raise ValueError('--from-delivery 仅供新任务 init；不恢复旧任务编辑权')
        if args.task_only_spec and (args.command != 'init' or not args.task_only or not args.task_id or args.task_read_policy):
            raise ValueError('任务专用引导文件仅供 task-only init 使用')
        if args.task_only and (not args.task_id or args.command == 'identity'):
            raise ValueError('task-only 必须显式指定任务，不能写共享身份')
        if args.task_only and args.command == 'init' and not args.task_only_spec:
            raise ValueError('task-only init 必须提供明确授权文件')
        spec = json.loads(external_control_bytes(args.task_only_spec, root)) if args.task_only_spec else None
        manager = task_only_scope(root, args.task_id, spec) if args.task_only else contextlib.nullcontext()
        metadata = task_metadata_scope(root, args.task_id) if args.task_only and (args.command in {'cancel', 'release-execution'} or args.command == 'migrate-task' and args.action in {'cancel', 'release-execution'}) else contextlib.nullcontext()
        with manager, metadata:
            if not (args.task_only and args.command in {'resume', 'complete-code'}):
                protocol_gate(root)
            if args.command == "identity":
                selection = {"bundleId": args.bundle_id, "scheme": args.scheme, "product": args.product, "module": args.module, "buildMode": args.build_mode}
                result = observe_identity(root, args.platform, selection)
                if args.write:
                    if any(field["status"] == "needs-confirmation" for field in result["fields"].values()):
                        raise ValueError("身份存在未知项，请明确确认字段，不允许写成成功")
                    with project_lock(root, ".ai-project/tasks.lock"):
                        checked = observe_identity(root, args.platform, selection)
                        if checked != result:
                            raise ValueError('确认身份期间配置变化，请重试只读预览')
                        atomic_json(root / ".ai-project/identity.json", checked)
            elif args.command in {'migrate-task', 'cancel', 'release-execution', 'complete-code'}:
                combined_ack = {'path': args.metadata_file, 'oldSha256': args.old_sha256, 'newSha256': args.new_sha256} if args.metadata_file else None
                result = lifecycle_transition(root, args.task_id, args.command if args.command != 'migrate-task' else args.action,
                    args.operation_id, args.reason, args.confirmed_by, args.authorization_source,
                    args.apply, args.preview_sha256, args.file, args.offline_writers_stopped, combined_ack)
            elif args.command == "init":
                policy = json.loads(bootstrap_bytes(args.task_read_policy.parent.resolve(), args.task_read_policy.name)) if args.task_read_policy else None
                result = begin(root, args.task_id, args.file, args.request, args.owner, args.protect, read_policy=policy, from_delivery=args.from_delivery, confirmed_by=args.confirmed_by, authorization_source=args.authorization_source)
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
            elif args.command == "verify-config":
                result = verify_configuration(root, args.task_id, args.profile)
            else:
                result = run_verification(args.project.expanduser(), args.task_id, args.profile, args.kind, args.argv, args.report or task_dir(root, args.task_id) / f"{args.kind}.json")
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
