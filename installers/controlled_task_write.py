#!/usr/bin/env python3
"""Partial-scope task-gated file transaction; never runs Git, builds, or tests."""
from __future__ import annotations
import argparse
import json
import sys
import os
import re
import stat
from contextlib import nullcontext
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'shared/task-routing'))
from read_boundary import ReadBoundary, task_read_scope, task_only_scope, bootstrap_bytes, secure_fd, external_control_bytes
from task_runtime import (check_scope, task_identity, protocol_gate, validate_state, task_record, digest, match, project_lock,
                          read_json, task_dir, task_state_paths)
from transactional_files import apply as apply_files, preview as preview_files, restore as restore_files


def safe_candidate(path: Path, project: Path) -> Path:
    """隔离根逐级拒绝链接，不通过 resolve 触及链接目标。"""
    if not path.is_absolute() or '..' in path.parts or path == project or path.is_relative_to(project) or project.is_relative_to(path):
        raise ValueError('隔离候选必须使用独立绝对路径')
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor = cursor / part
        mode = os.lstat(cursor).st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise ValueError('隔离根存在链接或非目录路径')
    return path


def inspect(project: Path, task_id: str, plan: dict) -> dict:
    """只核验任务与精确文件；项目范围保持 partial，不探测受限子树。"""
    boundary = ReadBoundary(project)
    if boundary.policy['mode'] != 'partial':
        raise ValueError('受控入口只接受明确 partial 的任务读取范围')
    if not Path(plan['root']).is_absolute() or Path(plan['root']) != project or Path(plan['root']).resolve(strict=True) != project:
        raise ValueError('计划项目根不一致')
    candidate = safe_candidate(Path(plan['candidate']), project)
    if not all(isinstance(plan.get(k), str) and plan[k].strip() for k in ('reviewedBy', 'reviewNote')):
        raise ValueError('必须记录预先审查者及来源与已有改动核对说明；该记录不代替授权')
    directory = task_dir(project, task_id)
    protocol_gate(project)
    state = task_record(project, directory / 'state.json')
    if state.get('pendingExecutions'):
        raise ValueError('存在尚未确认退出的验证写入者，禁止受控回写')
    if state['status'] != 'active':
        raise ValueError('仅活动任务可进行受控回写')
    if task_identity(project, state) != state.get('acceptedContext'):
        raise ValueError('任务身份变化')
    for other_path in task_state_paths(project):
        if other_path.parent == directory or other_path.parent.name.startswith('.init-'):
            continue
        other = task_record(project, other_path, allow_legacy=True)
        if other['status'] not in {'closed', 'cancelled'} and set(other['allowedFiles']) & set(state['allowedFiles']):
            raise ValueError('任务所有权冲突')
    scoped = check_scope(project, state)
    for name in state['allowedFiles']:
        if scoped['current'].get(name) != state['checkpoint'].get(name):
            raise ValueError('任务文件与检查点不一致，先人工审查')
    for item in plan['files']:
        name = item['path']
        boundary.require(name)
        if name not in state['allowedFiles'] or match(name, state['protectedGlobs']) or name.startswith(('.ai-project/', '.codex-rules/', '.agents/', '.migration-v2/')):
            raise ValueError('计划文件不属于可编辑任务范围')
        if item['beforeSha256'] != scoped['current'].get(name):
            raise ValueError('原工程文件与计划/检查点摘要不一致')
    reviewed = preview_files(plan)
    token = digest({'taskId': task_id, 'project': str(project), 'candidate': str(candidate), 'state': state,
                    'current': scoped['current'], 'files': reviewed, 'reviewedBy': plan['reviewedBy'], 'reviewNote': plan['reviewNote']})
    return {'status': 'dry-run', 'taskId': task_id, 'project': str(project), 'files': reviewed,
            'previewSha256': token, 'coverage': scoped['coverage'], 'currentSnapshot': scoped['current'],
            'limitations': ['original project is partial; Git and external commands are not run',
                            'preexisting changes require human review; Git dirty status is unverified',
                            'isolated evidence is not original-project verification',
                            'after apply review and checkpoint the task; no verification is inferred'], 'writes': []}


def controlled_write(project: Path, task_id: str, plan: dict, transaction: Path, apply: bool = False, preview_sha: str = '', task_only: bool = False) -> dict:
    """预览默认只读；应用在任务锁内重核快照，失败尝试按事务恢复。"""
    project = project.resolve(strict=True)
    with (task_only_scope(project, task_id) if task_only else nullcontext()), task_read_scope(project, task_id):
        relative = f'.ai-project/tasks/{task_id}/controlled-writes'
        boundary = ReadBoundary(project)
        boundary.require(relative)
        expected_parent = project / relative
        if not transaction.is_absolute() or transaction.parent != expected_parent or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', transaction.name):
            raise ValueError('备份事务必须位于当前任务的 controlled-writes 目录')
        if not apply:
            return inspect(project, task_id, plan)
        with project_lock(project, '.ai-project/tasks.lock'):
            reviewed = inspect(project, task_id, plan)
            if not preview_sha or reviewed['previewSha256'] != preview_sha:
                raise ValueError('预览摘要失效；请重新检查范围和文件差异')
            # 备份父目录必须预先存在且逐层无链接；避免使用链接作为事务存放点。
            if transaction.exists() or transaction.is_symlink():
                raise ValueError('事务目录已存在，禁止复用')
            fd = secure_fd(project, relative, directory=True)
            try:
                audit_name = transaction.name + '.audit.json'
                audit_fd = os.open(audit_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
                with os.fdopen(audit_fd, 'w', encoding='utf-8') as stream:
                    json.dump({'taskId': task_id, 'previewSha256': preview_sha, 'plan': plan,
                               'coverage': reviewed['coverage'], 'stateSnapshotSha256': digest(reviewed['currentSnapshot'])}, stream, ensure_ascii=False, indent=2)
                    stream.flush(); os.fsync(stream.fileno())
            finally:
                os.close(fd)
            try:
                result = apply_files(plan, transaction)
                # 若跨范围并发发生变化，立即拒绝；仅当无后续用户漂移时回滚。
                state = read_json(task_dir(project, task_id) / 'state.json')
                task_identity(project, state)
                actual = check_scope(project, state)['current']
                expected = dict(reviewed['currentSnapshot'])
                for item in reviewed['files']:
                    if item['afterSha256'] is None:
                        expected.pop(item['path'], None)
                    else:
                        expected[item['path']] = item['afterSha256']
                if actual != expected:
                    raise ValueError('写入期间声明范围有其他变化')
            except Exception as error:
                if (transaction / 'transaction.json').is_file():
                    try:
                        restore_files(transaction)
                    except Exception as restore_error:
                        raise ValueError(f'写入中断且恢复受阻；检查事务 {transaction}: {restore_error}') from error
                raise
            return {'status': 'applied-needs-review-and-checkpoint', 'transaction': str(transaction),
                    'previewSha256': preview_sha, 'coverage': reviewed['coverage'],
                    'files': result['files'], 'limitations': reviewed['limitations']}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--task-only', action='store_true')
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--transaction', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--preview-sha256', default='')
    args = parser.parse_args()
    try:
        if args.task_only:
            plan = json.loads(external_control_bytes(args.plan, args.project.resolve(strict=True)))
        else:
            plan_path = args.plan.resolve(strict=True)
            plan = json.loads(bootstrap_bytes(plan_path.parent, plan_path.name))
        print(json.dumps(controlled_write(args.project, args.task_id, plan, args.transaction, args.apply, args.preview_sha256, args.task_only), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({'status': 'blocked', 'error': str(error)}, ensure_ascii=False))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
