#!/usr/bin/env python3
"""Validate coverage and provenance; never claim automatic visual judgment."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
import sys

for parent in Path(__file__).resolve().parents:
    shared = parent / 'shared/task-routing'
    if (shared / 'read_boundary.py').is_file():
        sys.path.insert(0, str(shared))
        break
from read_boundary import ReadBoundary, require_complete, task_read_scope


def digest(value: dict) -> str:
    """稳定摘要独立覆盖合同，删减预期页面会使批准摘要失效。"""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def timestamp(value: str) -> bool:
    """证据时间必须是带时区的 ISO 时间；字符串存在不代表有效。"""
    try:
        return isinstance(value, str) and datetime.fromisoformat(value.replace('Z', '+00:00')).utcoffset() is not None
    except (ValueError, TypeError):
        return False


def argument_path(path: Path, project_argument: Path, project: Path) -> Path:
    """只映射 /var 等项目根别名；不 resolve 叶子，链接仍交由读取边界拒绝。"""
    try:
        name = path.expanduser().absolute().relative_to(project_argument.expanduser().absolute())
    except ValueError:
        name = path.expanduser().absolute().relative_to(project)
    return project / name


def validate_manifest(data: dict, base: Path, slice_id: str | None = None, current_binding: dict | None = None, expected: dict | None = None) -> dict:
    """装载显式任务范围，防止结束 binding 后意外退回项目全范围。"""
    if current_binding and current_binding.get('projectRoot') and current_binding.get('taskId'):
        with task_read_scope(Path(current_binding['projectRoot']), current_binding['taskId']):
            return _validate_manifest(data, base, slice_id, current_binding, expected)
    return _validate_manifest(data, base, slice_id, current_binding, expected)


def _validate_manifest(data: dict, base: Path, slice_id: str | None, current_binding: dict | None, expected: dict | None) -> dict:
    """核对独立预期、当前绑定及证据内容；人工真实性和视觉判断单独声明。"""
    errors, evidence_hashes = [], set()
    if not current_binding or data.get('binding') != current_binding:
        errors.append('missing/stale current task-code-profile binding')
    if not expected or data.get('coverageSha256') != digest(expected):
        errors.append('missing/changed independent expected coverage contract')
    if current_binding and expected and current_binding.get('uiCoverageSha256') != digest(expected):
        errors.append('expected coverage is not bound to approved plan')
    if expected and current_binding and expected.get('taskId') != current_binding.get('taskId'):
        errors.append('expected coverage belongs to another task')
    expected_states = {}
    for screen in (expected or {}).get('screens', []):
        if slice_id and screen.get('sliceId') != slice_id:
            continue
        for state in screen.get('states', []):
            key = (screen.get('sliceId'), screen.get('screenId'), state.get('id'))
            if key in expected_states or not all(key):
                errors.append('duplicate/invalid expected screen-state')
            expected_states[key] = state
    if not expected_states:
        errors.append('no independently expected states')
    observed = set()
    project = Path(current_binding['projectRoot']) if current_binding and current_binding.get('projectRoot') else None
    boundary = require_complete(project) if project else None

    def evidence(record: dict, side: str) -> None:
        """只从授权项目读常规证据文件；凭据和链接继续由统一边界拒绝。"""
        if not isinstance(record, dict) or not boundary:
            errors.append('legacy path-only evidence is unverified; recapture with provenance')
            return
        try:
            name = record.get('path', '')
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('evidence path must be project-relative')
            raw = boundary.read_bytes(name)
            sha = hashlib.sha256(raw).hexdigest()
            if record.get('sha256') != sha or record.get('binding') != current_binding or record.get('side') != side:
                raise ValueError('evidence hash/binding/side mismatch')
            media = record.get('mediaType')
            valid_media = (media == 'image/png' and raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw) > 32) or (media == 'image/jpeg' and raw.startswith(b'\xff\xd8') and raw.endswith(b'\xff\xd9')) or (media == 'video/mp4' and raw[4:8] == b'ftyp')
            if media == 'application/ui-tree+json':
                tree = json.loads(raw)
                valid_media = isinstance(tree, dict) and bool(tree.get('nodes'))
            if media == 'application/interaction-test+json':
                test = json.loads(raw)
                valid_media = isinstance(test, dict) and bool(test.get('steps')) and test.get('status') == 'passed'
            if not valid_media:
                raise ValueError('evidence content is not the declared UI media')
            if not timestamp(record.get('capturedAt')) or record.get('origin') != 'runtime-capture' or not record.get('captureCommand'):
                raise ValueError('capture provenance missing')
            if not record.get('artifactSha256') or not record.get('device') or not record.get('stateId'):
                raise ValueError('build/device/state identity missing')
            evidence_hashes.add(sha)
        except (OSError, ValueError, TypeError, KeyError) as error:
            errors.append(f'evidence: {error}')

    for screen in data.get('screens', []):
        if slice_id and screen.get('sliceId') != slice_id:
            continue
        for state in screen.get('states', []):
            key = (screen.get('sliceId'), screen.get('screenId'), state.get('id'))
            if key in observed:
                errors.append(f'duplicate state: {key}')
            observed.add(key)
            contract = expected_states.get(key)
            if contract is None:
                errors.append(f'unexpected state: {key}')
                continue
            if state.get('status') == 'not_applicable':
                if not contract.get('allowNotApplicable') or not state.get('notes') or not state.get('reviewedBy'):
                    errors.append(f'unapproved state exemption: {key}')
                continue
            if state.get('status') != 'verified':
                errors.append(f'unverified state: {key}')
                continue
            for field, side in [('sourceEvidence', 'source'), ('targetEvidence', 'target')]:
                records = state.get(field, [])
                if not records:
                    errors.append(f'{key}: missing {field}')
                for record in records:
                    if isinstance(record, dict) and record.get('stateId') != state.get('id'):
                        errors.append(f'{key}: evidence belongs to another state')
                    evidence(record, side)
            if contract.get('interactionRequired'):
                records = state.get('interactionEvidence', [])
                if not records or not any(isinstance(x, dict) and x.get('mediaType') in {'video/mp4', 'application/interaction-test+json'} for x in records):
                    errors.append(f'{key}: interaction process evidence required')
                for record in records:
                    if isinstance(record, dict) and record.get('stateId') != state.get('id'):
                        errors.append(f'{key}: interaction belongs to another state')
                    evidence(record, 'target')
    for missing in set(expected_states) - observed:
        errors.append(f'missing expected state: {missing}')
    review = data.get('review', {})
    if not review.get('reviewedBy') or not timestamp(review.get('reviewedAt')) or review.get('decision') != 'passed':
        errors.append('human visual/interaction review not recorded')
    return {'status': 'blocked' if errors else 'passed', 'checkedStates': len(observed), 'expectedStates': len(expected_states), 'errors': errors, 'evidenceHashes': sorted(evidence_hashes), 'assurance': 'coverage/provenance contract plus recorded human judgment; not automatic visual verification'}


def load_context(project: Path, task_id: str, profile: str, slice_id: str | None, expected_path: Path) -> tuple[dict, dict]:
    """从当前任务和已批准计划取得事实；调用者不能用自报绑定冒充当前代码。"""
    from task_runtime import binding
    from plan_approval import approval_status, digest as plan_digest
    with task_read_scope(project, task_id):
        boundary = require_complete(project)
        expected = boundary.read_json(expected_path.absolute().relative_to(project).as_posix())
        workspace = project / '.migration-workspace-v2'
        valid, reason = approval_status(workspace)
        if not valid:
            raise ValueError(reason)
        plan_sha, plan = plan_digest(workspace)
        current = binding(project, task_id, profile, slice_id)
        current.update(planSha256=plan_sha, uiCoverageSha256=plan.get('uiCoverageSha256'))
        return current, expected
