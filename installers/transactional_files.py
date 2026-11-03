#!/usr/bin/env python3
"""Explicit file transactions: preview, apply with drift checks, and restore."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'shared/task-routing'))
from task_runtime import atomic_json, lock
from read_boundary import bootstrap_bytes, protected_name


def relative(value: str) -> str:
    """仅允许明确的普通相对文件；拒绝凭据、Git 元数据和父级跳转。"""
    p = PurePosixPath(value)
    if p.is_absolute() or not p.parts or '..' in p.parts or str(p) != value or protected_name(value):
        raise ValueError(f'unsafe transaction path: {value}')
    return value


def content(root: Path, name: str) -> bytes | None:
    """逐层拒绝链接/特殊类型；缺失普通文件返回 None。"""
    name = relative(name)
    cursor = root
    for part in PurePosixPath(name).parts:
        cursor = cursor / part
        try:
            mode = cursor.lstat().st_mode
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError(f'linked/special transaction path: {name}')
    return bootstrap_bytes(root, name)


def sha(raw: bytes | None) -> str | None:
    """缺失与空文件使用不同身份。"""
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def write(root: Path, name: str, raw: bytes | None, mode: int = 0o644) -> None:
    """同目录原子替换；删除只针对事务记录的新文件。"""
    content(root, name)
    path = root / relative(name)
    if raw is None:
        if path.exists():path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix='.framework-transaction-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def preview(plan: dict) -> list[dict]:
    """全部文件先比较基线，任何用户漂移都会在首次写入前拒绝。"""
    root, candidate = Path(plan['root']).resolve(strict=True), Path(plan['candidate']).resolve(strict=True)
    results, seen = [], set()
    for item in plan['files']:
        name = relative(item['path'])
        if name in seen:raise ValueError('duplicate transaction path')
        seen.add(name)
        before, after = content(root, name), content(candidate, name)
        if sha(before) != item['beforeSha256'] or sha(after) != item['afterSha256']:
            raise ValueError(f'baseline/candidate drift: {name}')
        results.append({'path':name, 'beforeSha256':sha(before), 'afterSha256':sha(after), 'mode':item.get('mode', 0o644)})
    if not results:raise ValueError('empty transaction')
    return results


def apply(plan: dict, transaction: Path) -> dict:
    """先保存完整前像与计划，再逐文件应用；不清理未声明路径。"""
    if transaction.exists():raise ValueError('transaction directory already exists')
    reviewed = preview(plan)
    root, candidate = Path(plan['root']).resolve(), Path(plan['candidate']).resolve()
    transaction.mkdir(parents=True)
    with lock(transaction / 'transaction.lock'):
        for item in reviewed:
            raw = content(root, item['path'])
            if raw is not None:write(transaction / 'before', item['path'], raw, item['mode'])
        record = {'root':str(root), 'candidate':str(candidate), 'status':'applying', 'files':reviewed}
        atomic_json(transaction / 'transaction.json', record)
        for item in reviewed:
            # 在每次写之前再核对，防止预览后出现的新编辑被覆盖。
            if sha(content(root, item['path'])) != item['beforeSha256']:raise ValueError(f'concurrent edit: {item["path"]}')
            incoming = content(candidate, item['path'])
            if sha(incoming) != item['afterSha256']:raise ValueError('candidate changed after preview')
            write(root, item['path'], incoming, item['mode'])
        record['status'] = 'applied';atomic_json(transaction / 'transaction.json', record)
    return record


def restore(transaction: Path, dry_run: bool = False) -> dict:
    """允许中断恢复；存在事务后的用户修改时整次拒绝，不强制覆盖。"""
    record = json.loads((transaction / 'transaction.json').read_text())
    if record['status'] == 'restored':return record
    root = Path(record['root']).resolve(strict=True)
    changes = []
    for item in record['files']:
        actual = sha(content(root, item['path']))
        if actual not in {item['beforeSha256'], item['afterSha256']}:raise ValueError(f'local drift prevents restore: {item["path"]}')
        if actual == item['afterSha256']:
            raw = content(transaction / 'before', item['path']) if item['beforeSha256'] else None
            if sha(raw) != item['beforeSha256']:raise ValueError('backup hash mismatch')
            changes.append((item, raw))
    if dry_run:return {'status':'dry-run', 'restoreFiles':[x[0]['path'] for x in changes]}
    with lock(transaction / 'transaction.lock'):
        for item, raw in changes:
            if sha(content(root, item['path'])) != item['afterSha256']:raise ValueError('concurrent edit during restore')
            write(root, item['path'], raw, item['mode'])
        record['status'] = 'restored';atomic_json(transaction / 'transaction.json', record)
    return record


def main() -> int:
    """默认只预览；显式 --apply 或 restore 才修改记录中的文件。"""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--transaction', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args=parser.parse_args()
    if args.restore:result=restore(args.transaction, args.dry_run)
    else:
        if not args.plan:raise ValueError('--plan is required')
        plan=json.loads(args.plan.read_text())
        result=apply(plan,args.transaction) if args.apply and not args.dry_run else {'status':'dry-run', 'root':plan['root'], 'files':preview(plan), 'writes':[]}
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':
    raise SystemExit(main())
