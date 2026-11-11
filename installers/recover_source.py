#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recover an installed framework's exact source location, never its task baselines."""
from __future__ import annotations

import argparse
import ast
import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

from project_framework import (
    LOCK_PATHS, MANIFEST_RELATIVE, TASK_PROTOCOL, artifact_inventory,
    installation_locks, manifest_protocol, project_anchor, require_offline,
    runtime_state_schemas,
)
from read_boundary import (
    ReadBoundary, bootstrap_bytes, canonical_relative, policy_digest,
    protected_name, secure_fd, task_only_current,
)
from task_runtime import digest, validate_state

# 审计只保存恢复位置与原始摘要，不修改生命周期或任务材料。
AUDIT_DIRECTORY = '.ai-project/source-recoveries'
# 只接受完整摘要；空字符串不能代表缺失的安装事实。
SHA256 = re.compile(r'[0-9a-f]{64}')
# 与固定快照的活动源码目录一致；这些目录之外不作为可执行框架内容。
SOURCE_DIRECTORIES = {'frameworks', 'shared', 'installers', 'capabilities'}


def sha(raw: bytes) -> str:
    """返回原始字节摘要，避免格式化掩盖预览后的修改。"""
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value: dict) -> bytes:
    """统一审计及清单序列化；用于准确绑定提交后的文件摘要。"""
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def checked_root(value: Path) -> Path:
    """要求显式规范绝对目录；在解析前拒绝根及任意祖先符号链接。"""
    if not value.is_absolute() or '..' in value.parts:
        raise ValueError('root must be a canonical absolute directory')
    cursor = Path(value.anchor)
    for part in value.parts[1:]:
        cursor /= part
        info = cursor.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('root ancestors must be real directories, not links')
    return value


def snapshot(candidate: Path) -> dict:
    """不执行候选代码；安全逐层读取 pin 清单并用原始文件核对全部源码身份。"""
    raw_pin = bootstrap_bytes(candidate, '.framework-pin.json')
    pin = json.loads(raw_pin)
    if not isinstance(pin, dict) or not isinstance(pin.get('files'), dict) or not pin['files'] or not isinstance(pin.get('version'), str) or not isinstance(pin.get('suiteSha256'), str) or not SHA256.fullmatch(pin['suiteSha256']):
        raise ValueError('invalid immutable snapshot manifest')
    expected = pin['files']
    for name, item in expected.items():
        canonical_relative(name)
        if protected_name(name) or name not in {'VERSION', 'README.md'} and name.split('/')[0] not in SOURCE_DIRECTORIES:
            raise ValueError('snapshot inventory contains an unauthorized source path')
        if not isinstance(item, dict) or set(item) != {'sha256', 'mode'} or not isinstance(item['sha256'], str) or not SHA256.fullmatch(item['sha256']) or type(item['mode']) is not int or not 0 <= item['mode'] <= 0o777:
            raise ValueError('incomplete snapshot file identity')
    observed = {}
    runtime = None
    version = None

    def walk(relative: str | None = None) -> None:
        """只打开清单中的普通文件；拒绝额外文件、链接及特殊节点，不追随候选代码。"""
        nonlocal runtime, version
        descriptor = secure_fd(candidate, relative, directory=True) if relative else os.open(candidate, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            names = sorted(os.listdir(descriptor))
            for name in names:
                path = f'{relative}/{name}' if relative else name
                canonical_relative(path)
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    if not any(item.startswith(path + '/') for item in expected):
                        raise ValueError('unexpected directory in immutable snapshot: ' + path)
                    walk(path)
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('snapshot contains a linked or special file: ' + path)
                if path == '.framework-pin.json':
                    if bootstrap_bytes(candidate, path) != raw_pin:
                        raise ValueError('snapshot manifest changed during verification')
                    continue
                if path not in expected:
                    raise ValueError('snapshot contains an unlisted file: ' + path)
                raw = bootstrap_bytes(candidate, path)
                observed[path] = {'sha256': sha(raw), 'mode': info.st_mode & 0o777}
                if path == 'shared/task-routing/task_runtime.py':
                    runtime = raw.decode('utf-8')
                if path == 'VERSION':
                    version = raw.decode('utf-8').strip()
        finally:
            os.close(descriptor)

    walk()
    if observed != expected or version != pin['version']:
        raise ValueError('snapshot is incomplete or differs from its pin')
    suite_hash = digest({name: item['sha256'] for name, item in observed.items() if name.split('/')[0] in SOURCE_DIRECTORIES})
    if suite_hash != pin['suiteSha256']:
        raise ValueError('snapshot suite digest mismatch')
    parsed = ast.parse(runtime or '')
    protocols = [node.value.value for node in parsed.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'TASK_PROTOCOL' for target in node.targets) and isinstance(node.value, ast.Constant)]
    if protocols != [TASK_PROTOCOL]:
        raise ValueError('snapshot task protocol is unknown or incompatible')
    return {'version': version, 'suiteSha256': suite_hash, 'pinSha256': sha(raw_pin), 'files': observed, 'stateSchemas': sorted(runtime_state_schemas(runtime))}


def tasks(project: Path, boundary: ReadBoundary, schemas: list[str]) -> dict:
    """保留权威任务；旧材料只核直接元数据，不自动迁移、关闭或伪造材料批准。"""
    relative = boundary.require('.ai-project/tasks')
    try:
        descriptor = secure_fd(project, relative, directory=True)
    except FileNotFoundError:
        return {'states': {}, 'materials': {}}
    try:
        names = sorted(os.listdir(descriptor))
    finally:
        os.close(descriptor)
    states, materials = {}, {}
    for task_id in names:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', task_id) or task_id.startswith('.init-'):
            raise ValueError('unknown task directory or interrupted initialization')
        state_path = f'{relative}/{task_id}/state.json'
        if not boundary.exists(state_path):
            # 此操作不释放范围；只证明直接材料未出现已知持久任务标记。
            materials[task_id] = artifact_inventory(project, task_id)
            continue
        raw = boundary.read_bytes(state_path)
        state = validate_state(json.loads(raw), allow_legacy=True)
        if state['taskId'] != task_id or state['identity']['projectRoot'] != str(project):
            raise ValueError('task belongs to a different project or directory')
        if state['schemaVersion'] != '1.0' and state['schemaVersion'] not in schemas:
            raise ValueError('snapshot cannot read an existing task schema')
        if state.get('pendingExecutions'):
            raise ValueError('pending executions prevent source recovery: ' + task_id)
        states[task_id] = {'sha256': sha(raw), 'status': state['status']}
    return {'states': states, 'materials': materials}


@contextlib.contextmanager
def existing_lock(project: Path, boundary: ReadBoundary, relative: str):
    """只探测并持有已有锁，不创建 dry-run 文件；被真实执行者持有时立即拒绝。"""
    boundary.require(relative)
    try:
        descriptor = secure_fd(project, relative)
    except FileNotFoundError:
        yield
        return
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('live writer or verification lock prevents recovery: ' + relative) from error
        yield
    finally:
        os.close(descriptor)


def inspect(project: Path, candidate: Path, request: dict) -> tuple[dict, dict]:
    """生成精确预览；候选完整摘要必须等于原安装身份，pin 自报身份本身不能授权恢复。"""
    boundary = ReadBoundary(project)
    if task_only_current(project):
        raise ValueError('task-only read scope cannot recover global source ownership')
    raw_manifest = boundary.read_bytes(MANIFEST_RELATIVE.as_posix())
    manifest = json.loads(raw_manifest)
    if manifest_protocol(manifest) != TASK_PROTOCOL:
        raise ValueError('source recovery requires a complete protocol-2 installation')
    if not isinstance(manifest.get('suiteRoot'), str) or not manifest['suiteRoot'] or not isinstance(manifest.get('suiteSha256'), str) or not SHA256.fullmatch(manifest['suiteSha256']):
        raise ValueError('original installation source identity is incomplete')
    pinned = snapshot(candidate)
    if pinned['version'] != manifest.get('suiteVersion') or pinned['suiteSha256'] != manifest['suiteSha256']:
        raise ValueError('candidate is not the exact originally installed version and source digest')
    for item in manifest['managedFiles']:
        path = boundary.require(item['path'])
        source = canonical_relative(item['source'])
        actual = pinned['files'].get(source, {}).get('sha256')
        if actual != item['sourceSha256'] or actual != item['installedSha256'] or sha(boundary.read_bytes(path)) != actual:
            raise ValueError('managed source or project rule drift: ' + path)
    source_hashes = {item['source']: item['sourceSha256'] for item in manifest['managedFiles']}
    if 'contentSha256' in manifest and manifest['contentSha256'] != sha(json.dumps(source_hashes, sort_keys=True).encode()):
        raise ValueError('original managed source summary is inconsistent')
    inventory = tasks(project, boundary, pinned['stateSchemas'])
    preview = {
        'schemaVersion': '1.0', 'action': 'recover-source-location', 'request': request,
        'projectAnchor': project_anchor(project), 'readPolicySha256': policy_digest(boundary.policy),
        'manifestSha256': sha(raw_manifest), 'beforeSuiteRoot': manifest['suiteRoot'],
        'afterSuiteRoot': str(candidate), 'suiteVersion': pinned['version'],
        'suiteSha256': pinned['suiteSha256'], 'pinSha256': pinned['pinSha256'],
        'managedFilesSha256': digest(manifest['managedFiles']), 'tasks': inventory,
    }
    return preview, manifest


def atomic_control_json(project: Path, relative: str, value: dict) -> None:
    """在获准的安全父目录内原子写 JSON；拒绝链接，避免检查后路径被换出项目。"""
    boundary = ReadBoundary(project)
    relative = boundary.require(relative)
    boundary.exists(relative)
    parent, name = relative.rsplit('/', 1)
    descriptor = secure_fd(project, parent, directory=True)
    temporary = '.source-recovery-' + uuid.uuid4().hex
    try:
        mode = 0o600
        try:
            old = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
        except FileNotFoundError:
            pass
        else:
            try:
                info = os.fstat(old)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError('control target must be a single-link regular file')
                mode = info.st_mode & 0o777
            finally:
                os.close(old)
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=descriptor)
        with os.fdopen(handle, 'wb') as stream:
            stream.write(json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        try:
            os.unlink(temporary, dir_fd=descriptor)
        except FileNotFoundError:
            pass
        os.close(descriptor)


def ensure_audit_directory(project: Path) -> None:
    """仅创建已授权的审计子目录，不创建或修复项目的其他结构。"""
    boundary = ReadBoundary(project)
    boundary.require(AUDIT_DIRECTORY)
    descriptor = secure_fd(project, '.ai-project', directory=True)
    try:
        try:
            os.mkdir('source-recoveries', mode=0o700, dir_fd=descriptor)
        except FileExistsError:
            pass
    finally:
        os.close(descriptor)
    descriptor = secure_fd(project, AUDIT_DIRECTORY, directory=True)
    os.close(descriptor)


def recover(project: Path, candidate: Path, *, offline_writers_stopped: str = '', reason: str = '', confirmed_by: str = '', authorization_source: str = '', apply: bool = False, expected_preview_sha256: str | None = None) -> dict:
    """默认只读预览；精确授权和锁内重验后仅重绑定 suiteRoot，并支持中断后同操作重放。"""
    project, candidate = checked_root(project), checked_root(candidate)
    if project == candidate or project in candidate.parents or candidate in project.parents:
        raise ValueError('candidate snapshot must be outside the project')
    require_offline(offline_writers_stopped)
    for name, value in [('reason', reason), ('confirmed-by', confirmed_by), ('authorization-source', authorization_source)]:
        if not isinstance(value, str) or not value.strip():
            raise ValueError('missing explicit ' + name)
    if apply and (not isinstance(expected_preview_sha256, str) or not SHA256.fullmatch(expected_preview_sha256)):
        raise ValueError('apply requires the exact --expected-preview-sha256')
    request = {'projectRoot': str(project), 'candidateRoot': str(candidate), 'offlineWritersStoppedSource': offline_writers_stopped, 'reason': reason, 'confirmedBy': confirmed_by, 'authorizationSource': authorization_source}
    boundary = ReadBoundary(project)
    with contextlib.ExitStack() as locks:
        if apply:
            locks.enter_context(installation_locks(project))
        else:
            for path in LOCK_PATHS:
                locks.enter_context(existing_lock(project, boundary, path))
        preview, manifest = inspect(project, candidate, request)
        for task_id in preview['tasks']['states']:
            locks.enter_context(existing_lock(project, boundary, f'.ai-project/tasks/{task_id}/verification.lock'))
        latest, latest_manifest = inspect(project, candidate, request)
        if latest != preview or latest_manifest != manifest:
            raise ValueError('source, policy, manifest or task inventory changed while inspecting')
        preview_hash = digest(preview)
        result = {'status': 'dry-run', 'previewSha256': preview_hash, 'preview': preview, 'writes': []}
        if not apply:
            return result
        audit_path = f'{AUDIT_DIRECTORY}/{expected_preview_sha256}.json'
        boundary.require(audit_path)
        # 清单写成后审计收尾失败的重放，只接受同请求且与已写前像/后像完全绑定的记录。
        if boundary.exists(audit_path):
            audit = boundary.read_json(audit_path)
            if not isinstance(audit, dict) or audit.get('previewSha256') != expected_preview_sha256 or digest(audit.get('preview')) != expected_preview_sha256 or audit['preview'].get('request') != request or audit.get('status') not in {'prepared', 'applied'}:
                raise ValueError('existing recovery audit does not match this exact operation')
            if sha(boundary.read_bytes(MANIFEST_RELATIVE.as_posix())) == audit.get('afterManifestSha256') and manifest['suiteRoot'] == str(candidate):
                writes = []
                if audit['status'] != 'applied':
                    audit['status'] = 'applied'
                    atomic_control_json(project, audit_path, audit)
                    writes = [audit_path]
                return {'status': 'already-applied', 'previewSha256': expected_preview_sha256, 'audit': audit_path, 'writes': writes}
        else:
            audit = None
        if preview_hash != expected_preview_sha256:
            raise ValueError('preview expired or differs from the authorized recovery')
        if manifest['suiteRoot'] == str(candidate):
            return {'status': 'already-bound', 'previewSha256': preview_hash, 'writes': []}
        after = dict(manifest)
        after['suiteRoot'] = str(candidate)
        new_audit = {'schemaVersion': '1.0', 'status': 'prepared', 'previewSha256': preview_hash, 'preview': preview, 'beforeManifestSha256': preview['manifestSha256'], 'afterManifestSha256': sha(json_bytes(after)), 'recordedAt': datetime.now(timezone.utc).isoformat(), 'changedFields': ['suiteRoot']}
        if audit is not None and any(audit.get(key) != new_audit[key] for key in ('beforeManifestSha256', 'afterManifestSha256', 'changedFields')):
            raise ValueError('interrupted recovery audit has incompatible manifest identities')
        ensure_audit_directory(project)
        if audit is None:
            atomic_control_json(project, audit_path, new_audit)
            audit = new_audit
        # 再核授权边界与任务状态，绝不以早期预览替代提交时的事实。
        final, final_manifest = inspect(project, candidate, request)
        if final != preview or final_manifest != manifest:
            raise ValueError('recovery inputs changed before commit; manifest was not modified')
        atomic_control_json(project, MANIFEST_RELATIVE.as_posix(), after)
        if boundary.read_json(MANIFEST_RELATIVE.as_posix()) != after:
            raise ValueError('manifest outcome uncertain; inspect before retrying')
        audit['status'] = 'applied'
        atomic_control_json(project, audit_path, audit)
        return {'status': 'recovered', 'previewSha256': preview_hash, 'audit': audit_path, 'suiteRoot': str(candidate), 'writes': [MANIFEST_RELATIVE.as_posix(), audit_path], 'taskDataMigration': 'not-performed'}


def main() -> int:
    """暴露独立恢复入口；候选只是校验数据，不被导入或作为命令执行。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--offline-writers-stopped', required=True)
    parser.add_argument('--reason', required=True)
    parser.add_argument('--confirmed-by', required=True)
    parser.add_argument('--authorization-source', required=True)
    parser.add_argument('--expected-preview-sha256')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        result = recover(args.project, args.candidate, offline_writers_stopped=args.offline_writers_stopped, reason=args.reason, confirmed_by=args.confirmed_by, authorization_source=args.authorization_source, apply=args.apply, expected_preview_sha256=args.expected_preview_sha256)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError, SyntaxError) as error:
        print(json.dumps({'status': 'blocked', 'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
