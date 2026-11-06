#!/usr/bin/env python3
"""统一项目读取边界：白名单先于遍历/打开，拒绝链接、未知路径和静默漏检。"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import unicodedata
import re
import secrets
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path, PurePosixPath
from datetime import datetime, timezone, timedelta

# 仅此固定策略文件可作为读取授权的引导入口；不从项目源码猜测权限。
POLICY_PATH = '.ai-project/read-policy.json'
# 单次调用/线程的任务范围，不自动选取“最近任务”，关闭后不会变成共享项目约束。
_TASK_SCOPE = ContextVar('framework_task_read_scope', default=None)
_TASK_ONLY = ContextVar('framework_task_only_scope', default=None)
_TASK_METADATA = ContextVar('framework_task_metadata_scope', default=None)


@contextmanager
def task_read_scope(root: Path, task_id: str, policy: dict | None = None):
    """显式绑定当前任务读取范围；退出总是恢复调用前上下文，不改项目策略文件。"""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', task_id):
        raise BoundaryError('无效任务 ID')
    token = _TASK_SCOPE.set((root.resolve(), task_id, policy))
    try:
        yield
    finally:
        _TASK_SCOPE.reset(token)


@contextmanager
def task_only_scope(root: Path, task_id: str, spec: dict | None = None):
    """显式单任务引导；仅可在没有长期项目策略时使用，不创建共享授权。"""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', task_id):
        raise BoundaryError('无效任务 ID')
    token = _TASK_ONLY.set((root.resolve(), task_id, spec))
    try:
        yield
    finally:
        _TASK_ONLY.reset(token)


@contextmanager
def task_metadata_scope(root: Path, task_id: str):
    """仅终结/重放自身任务元数据；不延续终态业务或全局身份授权。"""
    token = _TASK_METADATA.set((root.resolve(), task_id))
    try:
        yield
    finally:
        _TASK_METADATA.reset(token)


def task_only_current(root: Path):
    active = _TASK_ONLY.get()
    return active if active and active[0] == root.resolve() else None


def validate_task_only(root: Path, task_id: str, spec: dict) -> dict:
    """只接受明确精确文件及身份输入，不接收目录通配或无来源的授权。"""
    allowed_keys = {'schemaVersion', 'scopeKind', 'confirmed', 'taskId', 'projectRoot', 'authorizationSource', 'scopeDescription', 'allowFiles', 'identityFiles', 'editableFiles', 'denyDirectories', 'platform', 'identitySelection'}
    if not isinstance(spec, dict) or set(spec) != allowed_keys or spec.get('schemaVersion') != '1.0' or spec.get('scopeKind') != 'task-only' or spec.get('confirmed') is not True or spec.get('projectRoot') != str(root) or spec.get('taskId') != task_id:
        raise BoundaryError('单任务引导根、任务或授权无效')
    if not all(isinstance(spec.get(k), str) and spec[k].strip() for k in ('authorizationSource', 'scopeDescription')):
        raise BoundaryError('单任务引导缺少授权来源或范围说明')
    files = spec.get('allowFiles'); identity = spec.get('identityFiles'); editable = spec.get('editableFiles')
    for group in (files, identity, editable):
        if not isinstance(group, list) or not group or len(set(group)) != len(group):
            raise BoundaryError('单任务文件列表必须非空且无重复')
        for name in group:
            if canonical_relative(name) != name or any(ch in name for ch in '*?[') or name.startswith('.ai-project/') or protected_name(name):
                raise BoundaryError('单任务只允许明确的非私密普通文件')
    if not set(identity).issubset(files) or not set(editable).issubset(files):
        raise BoundaryError('身份和编辑文件必须属于授权文件范围')
    denied = spec.get('denyDirectories')
    if not isinstance(denied, list) or len(set(denied)) != len(denied):
        raise BoundaryError('排除目录必须是无重复的精确路径列表')
    for directory in denied:
        if canonical_relative(directory) != directory or any(ch in directory for ch in '*?[') or directory == '.ai-project' or directory.startswith('.ai-project/'):
            raise BoundaryError('排除目录必须是非控制目录的规范精确路径')
        if any(name == directory or name.startswith(directory + '/') for name in files):
            raise BoundaryError('授权文件与明确排除目录冲突')
    selection = spec.get('identitySelection')
    if spec.get('platform') not in {'ios', 'harmonyos'} or not isinstance(selection, dict) or set(selection) - {'bundleId', 'scheme', 'product', 'module', 'buildMode'} or any(not isinstance(v, str) or len(v) > 256 for v in selection.values()):
        raise BoundaryError('单任务身份参数无效')
    return spec


def intersect_allow(left: list[str], right: list[str]) -> list[str]:
    """计算项目与任务白名单交集；任务只能收窄，不能授予项目未允许的路径。"""
    result = set()
    for first in left:
        for second in right:
            if first == '**':
                result.add(second)
            elif second == '**':
                result.add(first)
            elif first == second:
                result.add(first)
            elif first.endswith('/**') and (second == first[:-3] or second.startswith(first[:-2])):
                result.add(second)
            elif second.endswith('/**') and (first == second[:-3] or first.startswith(second[:-2])):
                result.add(first)
    return sorted(result)
# 这些目录和凭据即使出现在用户白名单也不读取；业务代码不是默认排除项。
PRIVATE_DIRS = {'.git', '.aws', '.ssh', '.gnupg'}
SECRET_SUFFIXES = {'.p12', '.pem', '.key', '.mobileprovision', '.cer'}
# 快照定义排除任务状态与生成缓存；它们仍在覆盖说明中明确列出。
SNAPSHOT_EXCLUDES = PRIVATE_DIRS | {'.ai-project', '.migration-workspace-v2', 'DerivedData', 'build', 'node_modules', '__pycache__'}


class BoundaryError(ValueError):
    """读取未获明确授权、文件类型不安全或读取失败；调用者必须报告阻塞。"""


def canonical_relative(value: str) -> str:
    """返回规范相对路径；拒绝绝对路径、父级跳转、别名拼写和反斜线。"""
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise BoundaryError('无效读取路径')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or str(path) != value or value == '.':
        raise BoundaryError(f'读取路径必须是规范项目相对路径: {value}')
    # NFC 统一配置与 macOS 目录枚举的表示；不会将 .. 或绝对路径变成合法路径。
    return unicodedata.normalize('NFC', value)


def protected_name(value: str) -> bool:
    """仅按路径名保守拒绝常见凭据；不打开、不摘要，也不保证发现所有秘密。"""
    path = PurePosixPath(unicodedata.normalize('NFC', value).casefold())
    name = path.name
    private_dirs = {part.casefold() for part in PRIVATE_DIRS} | {'credentials', 'credential', 'secrets', '.codex', '.claude', '.config'}
    credential_stems = r'(?:credentials?|secrets?|client[_-]secret|api[_-]key|private[_-]key|access[_-]token|refresh[_-]token|token)'
    return (bool(set(path.parts) & private_dirs) or any(part.endswith('.local') or '.local.' in part for part in path.parts) or name == '.env' or name.startswith('.env.')
            or path.suffix in SECRET_SUFFIXES | {'.p8', '.pfx', '.jks', '.keystore'}
            or bool(re.fullmatch(credential_stems + r'(?:[._-][a-z0-9][a-z0-9_-]*)*', name))
            or name in {'googleservice-info.plist', 'id_rsa', 'id_ed25519', '.netrc', '.npmrc'})


def external_protected_name(value: str) -> bool:
    """项目读取和临时外部许可共用同一套凭据拒绝规则。"""
    return protected_name(value)


def external_control_bytes(path: Path, project: Path) -> bytes:
    """只打开项目外的明确控制 JSON；逐级拒绝链接和硬链接。"""
    if not path.is_absolute() or '..' in path.parts or path.is_relative_to(project):
        raise BoundaryError('控制文件必须位于项目外的规范绝对路径')
    cursor = Path(path.anchor)
    for index, part in enumerate(path.parts[1:]):
        cursor = cursor / part
        observed = os.lstat(cursor)
        if stat.S_ISLNK(observed.st_mode):
            raise BoundaryError('控制文件路径不能包含链接')
        leaf = index == len(path.parts) - 2
        if (leaf and (not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1)) or (not leaf and not stat.S_ISDIR(observed.st_mode)):
            raise BoundaryError('控制文件必须是无硬链接普通文件')
    return bootstrap_bytes(path.parent, path.name)


def policy_digest(value: dict) -> str:
    """摘要完整读取策略；策略变化将使旧身份、任务和证据失效。"""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def secure_fd(root: Path, relative: str, directory: bool = False) -> int:
    """用逐层 openat/O_NOFOLLOW 打开路径；拒绝任何链接、设备和多重硬链接。

    返回文件描述符由调用方关闭。祖先目录使用已有描述符，避免检查后换成符号链接。
    本函数是策略引导层；业务调用必须先通过 ReadBoundary.require。
    """
    name = canonical_relative(relative)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(root, flags | os.O_DIRECTORY)
    try:
        parts = name.split('/')
        for index, part in enumerate(parts):
            is_directory = index < len(parts) - 1 or directory
            child = os.open(part, flags | (os.O_DIRECTORY if is_directory else os.O_NONBLOCK), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        observed = os.fstat(descriptor)
        if not (stat.S_ISDIR(observed.st_mode) if directory else stat.S_ISREG(observed.st_mode)):
            raise BoundaryError(f'读取目标不是普通文件/目录: {name}')
        if not directory and observed.st_nlink != 1:
            raise BoundaryError(f'拒绝多重硬链接，无法证明内容归属: {name}')
        result, descriptor = descriptor, -1
        return result
    except OSError as error:
        if isinstance(error, FileNotFoundError):
            raise
        raise BoundaryError(f'读取路径包含链接、不可访问目录或不安全类型: {name}: {error.__class__.__name__}') from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def bootstrap_bytes(root: Path, relative: str) -> bytes:
    """读取固定授权入口；不跟随链接，返回原始字节，调用方负责 JSON 校验。"""
    with os.fdopen(secure_fd(root, relative), 'rb') as stream:
        return stream.read()


_EXTERNAL_PERMITS = {}
_EXTERNAL_LOCK = threading.Lock()


def _check_external_holds(project: Path) -> None:
    """无法证明历史暂停与外部读取无关时保守停止，不接受改 ID 重放。"""
    boundary = ReadBoundary(project)
    name = '.ai-project/operation-holds.json'
    if boundary.exists(name) and any(item.get('status') == 'hold' for item in boundary.read_json(name).get('operations', [])):
        raise BoundaryError('存在未解除的操作暂停；无法仅凭新 operationId 证明外部读取无关')


def _check_external_identity(project: Path, state: dict) -> None:
    """核对任务的项目身份与已确认 revision；受限范围不能伪造完整身份。"""
    from task_runtime import task_identity, protocol_gate
    protocol_gate(project)
    if task_identity(project, state) != state.get('acceptedContext'):
        raise BoundaryError('任务项目身份或 revision 已变化')


def issue_external_permit(project: Path, task_id: str, grant: dict) -> str:
    """仅在当前进程登记明确授权；不持久化、不探测外部路径。"""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', task_id or ''):
        raise BoundaryError('无效任务 ID')
    project = project.resolve(strict=True)
    from task_runtime import project_lock
    with task_read_scope(project, task_id), project_lock(project, '.ai-project/tasks.lock'):
        boundary = ReadBoundary(project)
        state = boundary.read_json(f'.ai-project/tasks/{task_id}/state.json')
        if state.get('taskId') != task_id or state.get('status') != 'active':
            raise BoundaryError('任务未激活')
        if grant.get('taskId') != task_id or grant.get('projectRoot') != str(project) or grant.get('scopeKind') != 'external-once' or grant.get('confirmed') is not True or not str(grant.get('authorizationSource', '')).strip():
            raise BoundaryError('缺少当前任务明确的外部授权来源')
        if state.get('readCoverage') != boundary.coverage():
            raise BoundaryError('任务授权范围变化')
        _check_external_holds(project)
        _check_external_identity(project, state)
        authorization_id = grant.get('authorizationId')
        if not isinstance(authorization_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{15,79}', authorization_id):
            raise BoundaryError('缺少唯一授权 ID')
        name = canonical_relative(grant.get('file'))
        if external_protected_name(name) or '*' in name or '?' in name or '[' in name:
            raise BoundaryError('文件必须是精确的非凭据路径')
        external = grant.get('externalRoot')
        if not isinstance(external, str) or not external.startswith('/') or external == '/' or external_protected_name(external.lstrip('/')):
            raise BoundaryError('外部根无效')
        canonical_relative(external.lstrip('/'))
        external_path = Path(external)
        if external_path.is_relative_to(project) or project.is_relative_to(external_path):
            raise BoundaryError('外部根不能与项目重叠或成为项目祖先')
        if not isinstance(grant.get('rootDevice'), int) or not isinstance(grant.get('rootInode'), int):
            raise BoundaryError('缺少外部根身份')
        for field in ('fileDevice', 'fileInode', 'fileSize', 'fileMtimeNs', 'fileCtimeNs'):
            if type(grant.get(field)) is not int or grant[field] < 0:
                raise BoundaryError('缺少授权文件的精确身份')
        try:
            expires = datetime.fromisoformat(grant['expiresAt'].replace('Z', '+00:00'))
            if expires.utcoffset() is None or expires <= datetime.now(timezone.utc) or expires > datetime.now(timezone.utc) + timedelta(hours=24):
                raise ValueError('expired')
        except (KeyError, ValueError, TypeError) as error:
            raise BoundaryError('授权过期或时间无效') from error
        token = secrets.token_urlsafe(32)
        project_identity = os.stat(project)
        record = {'grant': dict(grant), 'stateSha256': policy_digest(state), 'coverage': boundary.coverage(), 'projectDevice':project_identity.st_dev, 'projectInode':project_identity.st_ino}
        # 只持久化不可复用的审计摘要；该文件不包含许可，也不能用于恢复授权。
        audit_name = f'.ai-project/tasks/{task_id}/external-authorization-audit/{authorization_id}.json'
        boundary.require(audit_name)
        task_fd = secure_fd(project, f'.ai-project/tasks/{task_id}', directory=True)
        try:
            try:
                os.mkdir('external-authorization-audit', 0o700, dir_fd=task_fd)
            except FileExistsError:
                pass
            audit_fd = os.open('external-authorization-audit', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=task_fd)
            try:
                fd = os.open(authorization_id + '.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=audit_fd)
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump({'taskId':task_id, 'authorizationId':authorization_id, 'grantSha256':policy_digest(grant), 'stateSha256':record['stateSha256'], 'issuedAt':datetime.now(timezone.utc).isoformat()}, stream, sort_keys=True)
                    stream.flush(); os.fsync(stream.fileno())
            finally:
                os.close(audit_fd)
        finally:
            os.close(task_fd)
        with _EXTERNAL_LOCK:
            _EXTERNAL_PERMITS[token] = record
        return token


def revoke_external_permit(token: str) -> None:
    """主动撤销进程内许可；进程结束会自然丢失所有许可。"""
    with _EXTERNAL_LOCK:
        _EXTERNAL_PERMITS.pop(token, None)


def read_external_once(project: Path, task_id: str, token: str, relative: str) -> bytes:
    """一次性精确读取；授权只在内存，平台权限仍由操作系统强制。"""
    with _EXTERNAL_LOCK:
        record = _EXTERNAL_PERMITS.pop(token, None)
    if record is None:
        raise BoundaryError('许可不存在、已使用或属于其他进程')
    grant = record['grant']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', task_id or ''):
        raise BoundaryError('无效任务 ID')
    project = project.resolve(strict=True)
    if grant.get('taskId') != task_id or grant.get('projectRoot') != str(project):
        raise BoundaryError('许可属于其他任务或项目')
    project_identity = os.stat(project)
    if (project_identity.st_dev, project_identity.st_ino) != (record['projectDevice'], record['projectInode']):
        raise BoundaryError('项目根身份变化')
    with task_read_scope(project, task_id):
        boundary = ReadBoundary(project)
        state = boundary.read_json(f'.ai-project/tasks/{task_id}/state.json')
        if state.get('taskId') != task_id or state.get('status') != 'active':
            raise BoundaryError('任务未激活')
        if policy_digest(state) != record['stateSha256'] or boundary.coverage() != record['coverage']:
            raise BoundaryError('任务状态或授权范围变化')
        _check_external_identity(project, state)
        if grant.get('taskId') != task_id or grant.get('projectRoot') != str(project) or grant.get('scopeKind') != 'external-once' or grant.get('confirmed') is not True or not str(grant.get('authorizationSource', '')).strip():
            raise BoundaryError('缺少当前任务明确的外部授权来源')
        if grant.get('file') != relative or external_protected_name(relative):
            raise BoundaryError('外部文件不在精确授权内或属于凭据')
        _check_external_holds(project)
        canonical_relative(relative)
        try:
            expires = datetime.fromisoformat(grant['expiresAt'].replace('Z', '+00:00'))
            if expires.utcoffset() is None or expires <= datetime.now(timezone.utc):
                raise ValueError('expired')
        except (KeyError, ValueError, TypeError) as error:
            raise BoundaryError('授权过期或时间无效') from error
        external = grant.get('externalRoot')
        if not isinstance(external, str) or not external.startswith('/') or external == '/' or external_protected_name(external.lstrip('/')):
            raise BoundaryError('外部根无效')
        root_relative = canonical_relative(external.lstrip('/'))
        root_fd = secure_fd(Path('/'), root_relative, directory=True)
        descriptor = root_fd
        try:
            info = os.fstat(root_fd)
            if grant.get('rootDevice') != info.st_dev or grant.get('rootInode') != info.st_ino:
                raise BoundaryError('外部根身份变化')
            for index, part in enumerate(relative.split('/')):
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | (os.O_DIRECTORY if index < len(relative.split('/')) - 1 else os.O_NONBLOCK)
                child = os.open(part, flags, dir_fd=descriptor)
                if descriptor != root_fd: os.close(descriptor)
                descriptor = child
            target = os.fstat(descriptor)
            if not stat.S_ISREG(target.st_mode) or target.st_nlink != 1 or target.st_size > 1024 * 1024:
                raise BoundaryError('外部文件类型、链接数或大小不允许')
            if (target.st_dev, target.st_ino, target.st_size, target.st_mtime_ns, target.st_ctime_ns) != (grant['fileDevice'], grant['fileInode'], grant['fileSize'], grant['fileMtimeNs'], grant['fileCtimeNs']):
                raise BoundaryError('授权文件身份变化；需要新的明确授权')
            with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                result = stream.read(1024 * 1024 + 1)
            after = os.fstat(descriptor)
            if (target.st_dev, target.st_ino, target.st_size, target.st_mtime_ns, target.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise BoundaryError('外部文件读取期间发生变化')
            if len(result) > 1024 * 1024 or policy_digest(boundary.read_json(f'.ai-project/tasks/{task_id}/state.json')) != record['stateSha256'] or ReadBoundary(project).coverage() != record['coverage'] or datetime.now(timezone.utc) >= expires:
                raise BoundaryError('读取期间状态、范围或有效期变化')
            _check_external_holds(project)
            _check_external_identity(project, state)
            return result
        finally:
            if descriptor != root_fd: os.close(descriptor)
            os.close(root_fd)


class ReadBoundary:
    """一个规范项目根的一份读取授权；缺失或无效策略一律拒绝，不推导全量权限。"""

    def __init__(self, root: Path, policy: dict | None = None):
        """校验策略、根和白名单；仅固定 read-policy 可在策略建立前读取。"""
        self.root = root.resolve(strict=True)  # 根身份在策略中锁定，子路径从不 resolve 后跟随链接。
        only = task_only_current(self.root)
        if only is not None:
            try:
                fd = secure_fd(self.root, POLICY_PATH)
            except FileNotFoundError:
                pass
            else:
                os.close(fd)
                raise BoundaryError('存在长期项目策略；单任务引导不能绕过，应使用既有任务范围')
            task_id, supplied = only[1], only[2]
            relative = f'.ai-project/tasks/{task_id}/task-only.json'
            try:
                stored = json.loads(bootstrap_bytes(self.root, relative))
            except FileNotFoundError:
                stored = None
            try:
                saved_state = json.loads(bootstrap_bytes(self.root, f'.ai-project/tasks/{task_id}/state.json'))
            except FileNotFoundError:
                saved_state = None
            if saved_state is not None and stored is None:
                raise BoundaryError('已发布任务的策略丢失；不能用新参数重建')
            if supplied is not None and stored is not None and supplied != stored:
                raise BoundaryError('单任务策略与已发布内容不一致')
            spec = validate_task_only(self.root, task_id, supplied if supplied is not None else stored)
            if saved_state is not None and (saved_state.get('taskId') != task_id or saved_state.get('readCoverage', {}).get('policySha256') != policy_digest(spec)):
                raise BoundaryError('单任务策略与已发布基线不一致，禁止读取其声明文件')
            metadata_only = _TASK_METADATA.get() == (self.root, task_id)
            if saved_state is not None and saved_state.get('status') in {'closed', 'cancelled'} and not metadata_only:
                raise BoundaryError('单任务已结束；授权不延续到后续任务')
            self.policy = {'schemaVersion':'1.0','confirmed':True,'projectRoot':str(self.root),'scopeKind':'task', 'taskId':task_id,
                           'mode':'partial','scopeDescription':spec['scopeDescription'], 'identityFiles':spec['identityFiles'], 'identityGitMode':'unverified',
                           'allow': sorted(set(spec['allowFiles'] + [f'.ai-project/tasks/{task_id}/**', '.ai-project/tasks.lock', '.ai-project/framework-installation.json', '.ai-project/protected-paths.json'])),
                           'deny': [name + '/**' for name in spec['denyDirectories']]}
            if metadata_only:
                self.policy['allow'] = [f'.ai-project/tasks/{task_id}/**', '.ai-project/tasks.lock', '.ai-project/framework-installation.json']
            self.allow = self._entries(self.policy['allow'])
            self.deny = self._entries(self.policy['deny'], empty=True)
            self.project_policy = dict(self.policy)
            self.task_id, self.task_policy = task_id, spec
            self.sha256 = policy_digest(spec)
            return
        try:
            self.policy = policy if policy is not None else json.loads(bootstrap_bytes(self.root, POLICY_PATH))
        except (OSError, ValueError) as error:
            raise BoundaryError('缺少或无法安全读取 .ai-project/read-policy.json；先明确可读路径，禁止默认全量扫描') from error
        if not isinstance(self.policy, dict) or self.policy.get('schemaVersion') != '1.0' or self.policy.get('confirmed') is not True:
            raise BoundaryError('读取策略格式或确认字段无效')
        if self.policy.get('projectRoot') != str(self.root):
            raise BoundaryError('读取策略属于其他项目根')
        if self.policy.get('mode') not in {'complete', 'partial'} or not str(self.policy.get('scopeDescription', '')).strip():
            raise BoundaryError('读取策略必须声明完整/部分模式和范围说明')
        self.allow = self._entries(self.policy.get('allow'))
        self.deny = self._entries(self.policy.get('deny', []), empty=True)
        if self.policy['mode'] == 'complete' and (self.allow != ['**'] or self.deny):
            raise BoundaryError('完整模式必须明确允许 ** 且无业务排除；受限范围必须标部分')
        if self.policy.get('scopeKind', 'project') not in {'project', 'legacy-project'}:
            raise BoundaryError('项目入口不能装载任务级策略，临时范围必须放在对应任务目录')
        # 保留项目原文及其来源；任务覆盖不写回这份长期授权。
        self.project_policy = dict(self.policy)
        self.task_id = None
        self.task_policy = None
        self.sha256 = policy_digest(self.policy)
        active = _TASK_SCOPE.get()
        if active is not None and active[0] == self.root:
            self._apply_task(active[1], active[2])

    def _apply_task(self, task_id: str, supplied: dict | None) -> None:
        """只装载显式任务 ID 的临时范围；文件丢失或篡改不降级为项目全范围。"""
        relative = f'.ai-project/tasks/{task_id}/read-policy.json'
        self.require(relative)
        overlay = supplied
        if supplied is not None:
            try:
                stored = json.loads(bootstrap_bytes(self.root, relative))
            except FileNotFoundError:
                stored = None
            if stored is not None and stored != supplied:
                raise BoundaryError('已发布任务策略不能由内存参数替换；新范围使用新任务 ID')
            if stored is None:
                state_path = f'.ai-project/tasks/{task_id}/state.json'
                self.require(state_path)
                try:
                    previous = json.loads(bootstrap_bytes(self.root, state_path))
                except FileNotFoundError:
                    previous = {}
                if previous.get('readCoverage', {}).get('scopeKind') == 'task':
                    raise BoundaryError('任务策略丢失，不能用新内存参数绕过恢复检查')
        if overlay is None:
            try:
                overlay = json.loads(bootstrap_bytes(self.root, relative))
            except FileNotFoundError:
                state_path = f'.ai-project/tasks/{task_id}/state.json'
                self.require(state_path)
                try:
                    state = json.loads(bootstrap_bytes(self.root, state_path))
                except FileNotFoundError:
                    return  # 尚未发布的新任务，继承项目范围，不创建任何新授权。
                if state.get('readCoverage', {}).get('scopeKind') == 'task':
                    raise BoundaryError('任务读取策略丢失，不能回退为全项目读取')
                return
        if not isinstance(overlay, dict) or overlay.get('schemaVersion') != '1.0' or overlay.get('confirmed') is not True or overlay.get('scopeKind') != 'task' or overlay.get('taskId') != task_id or overlay.get('projectRoot') != str(self.root):
            raise BoundaryError('任务读取范围的来源、根或任务 ID 无效')
        allow = self._entries(overlay.get('allow'))
        deny = self._entries(overlay.get('deny', []), empty=True)
        if overlay.get('mode') not in {'complete', 'partial'} or not overlay.get('scopeDescription'):
            raise BoundaryError('任务读取范围必须说明模式和用途')
        if overlay['mode'] == 'complete' and (allow != ['**'] or deny):
            raise BoundaryError('任务完整模式与实际范围矛盾')
        self.task_id, self.task_policy = task_id, overlay
        self.allow = intersect_allow(self.allow, allow)
        self.deny = sorted(set(self.deny + deny))
        self.policy = {**self.project_policy, 'scopeKind': 'task', 'taskId': task_id, 'mode': 'complete' if self.project_policy['mode'] == overlay['mode'] == 'complete' else 'partial', 'scopeDescription': overlay['scopeDescription'], 'allow': self.allow, 'deny': self.deny}
        self.sha256 = policy_digest({'project': self.project_policy, 'task': overlay})

    @staticmethod
    def _entries(values: list, empty: bool = False) -> list[str]:
        """只接受精确文件、目录/** 或显式 **；拒绝含糊 glob 和重复别名。"""
        if not isinstance(values, list) or (not values and not empty):
            raise BoundaryError('读取白名单必须为非空列表')
        result = []
        for value in values:
            if not isinstance(value, str):
                raise BoundaryError('读取条目必须是字符串')
            prefix = value[:-3] if value.endswith('/**') else value
            normalized = '**' if value == '**' else canonical_relative(prefix) + ('/**' if value.endswith('/**') else '')
            if value != '**' and any(char in prefix for char in '*?['):
                raise BoundaryError('读取范围不接受含糊通配符')
            if normalized in result:
                raise BoundaryError('读取范围存在重复规范路径')
            result.append(normalized)
        return sorted(result)

    @staticmethod
    def _matches(name: str, entries: list[str]) -> bool:
        """按目录边界匹配，不让 SourcesSafe 意外命中 Sources。"""
        return any(item == '**' or name == item or (item.endswith('/**') and (name == item[:-3] or name.startswith(item[:-2]))) for item in entries)

    def allowed(self, relative: str) -> bool:
        """纯名字判断授权，必须早于 stat、遍历或打开内容。"""
        name = canonical_relative(relative)
        task_state = bool(task_only_current(self.root) and re.fullmatch(r'\.ai-project/tasks/[A-Za-z0-9][A-Za-z0-9_.-]{0,79}/state\.json', name))
        return not protected_name(name) and (task_state or self._matches(name, self.allow)) and not self._matches(name, self.deny)

    def require(self, relative: str) -> str:
        """返回已授权的规范路径；未知、拒绝和越界在任何文件访问前抛错。"""
        name = canonical_relative(relative)
        if not self.allowed(name):
            raise BoundaryError(f'读取未获授权或明确受限: {name}')
        return name

    def read_bytes(self, relative: str) -> bytes:
        """安全读取已授权普通文件，不跟随祖先或叶子链接。"""
        name = self.require(relative)
        return bootstrap_bytes(self.root, name)

    def read_text(self, relative: str) -> str:
        """读取已授权 UTF-8 文本；编码失败不静默替换或算通过。"""
        return self.read_bytes(relative).decode('utf-8')

    def read_json(self, relative: str) -> dict:
        """读取授权 JSON；格式错误直接阻塞，返回值不自动填默认成功状态。"""
        return json.loads(self.read_text(relative))

    def exists(self, relative: str) -> bool:
        """只探测已授权普通文件；未知路径不先查询存在性，缺失返回 False。"""
        name = self.require(relative)
        try:
            descriptor = secure_fd(self.root, name)
        except FileNotFoundError:
            return False
        os.close(descriptor)
        return True

    def files(self, excludes: set[str] | None = None, subtree: str | None = None) -> list[str]:
        """只遍历明确允许的子树；受限目录不进入，精确文件不枚举其兄弟目录。

        链接或不可读的已授权项目使本次读取阻塞，不能跳过后声称完整。
        未列出的子树没有检查，部分覆盖说明会明确指出这一点。
        """
        excluded = excludes or set()
        result = set()  # 防止重叠白名单导致重复读取和不稳定摘要。

        def walk(relative: str):
            """在已授权目录描述符中枚举；名字授权完成后才查询文件类型。"""
            descriptor = secure_fd(self.root, relative, True) if relative else os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                with os.scandir(descriptor) as entries:
                    names = sorted(entry.name for entry in entries)
                normalized = [unicodedata.normalize('NFC', name) for name in names]
                if len(normalized) != len(set(normalized)):
                    raise BoundaryError('目录存在规范化后重名，不能可靠绑定内容')
                for disk_name, name in zip(names, normalized):
                    candidate = f'{relative}/{name}' if relative else name
                    if name in excluded or not self.allowed(candidate):
                        continue
                    observed = os.stat(disk_name, dir_fd=descriptor, follow_symlinks=False)
                    if stat.S_ISLNK(observed.st_mode):
                        raise BoundaryError(f'拒绝授权子树内的符号链接: {candidate}')
                    if stat.S_ISDIR(observed.st_mode):
                        walk(candidate)
                    elif stat.S_ISREG(observed.st_mode) and observed.st_nlink == 1:
                        result.add(candidate)
                    else:
                        raise BoundaryError(f'拒绝非普通或多重硬链接文件: {candidate}')
            finally:
                os.close(descriptor)

        entries = [self.require(subtree) + "/**"] if subtree else self.allow
        for item in entries:
            if item == '**':
                walk('')
            elif item.endswith('/**'):
                prefix = item[:-3]
                if any(part in excluded for part in PurePosixPath(prefix).parts) or not self.allowed(prefix):
                    continue
                try:
                    walk(prefix)
                except FileNotFoundError:
                    pass  # 尚未创建的已授权控制目录不当作已检查内容。
            elif not any(part in excluded for part in PurePosixPath(item).parts) and self.allowed(item) and self.exists(item):
                result.add(item)
        return sorted(result)

    def coverage(self) -> dict:
        """稳定描述授权完整性；部分范围永远不能因命令 exit 0 变成完整通过。"""
        result = {'status': self.policy['mode'], 'scopeKind': 'task' if self.task_policy else self.project_policy.get('scopeKind', 'legacy-project'), 'taskId': self.task_id, 'projectPolicySha256': policy_digest(self.project_policy), 'taskPolicySha256': policy_digest(self.task_policy) if self.task_policy else None, 'policySha256': self.sha256, 'scopeDescription': self.policy['scopeDescription'], 'allow': self.allow, 'deny': self.deny, 'uninspected': (['outside-allowlist', 'explicit-deny'] if self.policy['mode'] == 'partial' else []) + ['credential-name-exclusions', 'unnamed-secrets-not-detectable'], 'standardExclusions': sorted(SNAPSHOT_EXCLUDES) + ['credential-name-policy-v3', '.DS_Store'], 'credentialExclusionPolicy': 'name-policy-v3; excluded paths are not inspected; unnamed secrets are not detectable', 'definition': 'business-and-rules; excludes generated caches, private credentials and task state'}
        if task_only_current(self.root):
            result['projectPolicySha256'] = None
            result['policySource'] = 'task-only-no-project-policy'
            result['taskMetadataAccess'] = 'own task metadata and other task state files only'
        return result


def require_complete(root: Path) -> ReadBoundary:
    """外部进程不能由 Python 白名单约束；部分读取模式禁止运行编译/测试或旧迁移链。"""
    boundary = ReadBoundary(root)
    if boundary.policy['mode'] != 'complete':
        raise BoundaryError('部分读取范围不能约束外部命令/旧迁移链，已在执行前阻塞')
    return boundary
