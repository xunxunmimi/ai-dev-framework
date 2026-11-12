#!/usr/bin/env python3
"""Register explicitly authorized missing migration policies, never infer consent from paths."""
from __future__ import annotations
import fcntl
import json
import os
import secrets
import stat
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'shared/task-routing'))
from read_boundary import BoundaryError, POLICY_PATH, ReadBoundary, bootstrap_bytes, require_complete, secure_fd

STAGE_PREFIX = '.read-policy-bootstrap-'


def canonical_root(path: Path) -> Path:
    """Require an existing absolute directory without symlinks in the supplied path."""
    path = path.expanduser()
    if not path.is_absolute() or '..' in path.parts:
        raise BoundaryError('迁移根必须为已存在的绝对目录，不允许父级跳转')
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        info = cursor.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise BoundaryError(f'迁移根包含链接或不是目录: {cursor}')
    return path.resolve(strict=True)


def policy_for(root: Path, authorization: str) -> dict:
    """The caller records an actual user directive, not an automatically manufactured grant."""
    return {'schemaVersion': '1.0', 'scopeKind': 'project', 'confirmed': True,
            'projectRoot': str(root), 'mode': 'complete', 'allow': ['**'], 'deny': [],
            'scopeDescription': '用户明确授权读取本项目业务代码以进行 iOS 到鸿蒙迁移；凭据排除和平台权限仍生效',
            'authorizationSource': authorization, 'bootstrapKind': 'migration-explicit-v1'}


def inspect(root: Path) -> bool:
    """Read only bootstrap metadata. Existing restricted or broken policy is never replaced."""
    try:
        bootstrap_bytes(root, POLICY_PATH)
    except FileNotFoundError:
        try:
            directory = secure_fd(root, '.ai-project', directory=True)
        except FileNotFoundError:
            pass
        else:
            try:
                for entry in os.listdir(directory):
                    suffix = entry[len(STAGE_PREFIX):] if entry.startswith(STAGE_PREFIX) else ''
                    own = entry == '.read-policy-bootstrap.lock' or (len(suffix) == 32 and all(ch in '0123456789abcdef' for ch in suffix))
                    info = os.stat(entry, dir_fd=directory, follow_symlinks=False)
                    if not own or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise BoundaryError(f'{root}: 已有控制元数据但策略丢失，需恢复原策略，禁止重建授权')
            finally:
                os.close(directory)
        for name in ('.migration-v2', '.migration-workspace-v2', '.migration-workspace', '.codex-rules'):

            # Only inspect existence/type, never read project data before policy authorization.
            try:
                (root / name).lstat()
            except FileNotFoundError:
                continue
            raise BoundaryError(f'{root}: 已有安装/任务元数据但策略丢失，需恢复原策略，禁止重建授权')
        return False
    require_complete(root)
    return True


@contextmanager
def locked_directory(root: Path):
    """Hold a no-follow metadata descriptor and persistent flock; no lock unlink races."""
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    directory = lock = None
    try:
        try:
            os.mkdir('.ai-project', 0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        directory = os.open('.ai-project', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            lock = os.open('.read-policy-bootstrap.lock', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        except FileExistsError:
            lock = os.open('.read-policy-bootstrap.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BoundaryError('策略初始化锁不是普通单链接文件')
        fcntl.flock(lock, fcntl.LOCK_EX)
        actual = secure_fd(root, '.ai-project', directory=True)
        try:
            current, held = os.fstat(actual), os.fstat(directory)
            if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
                raise BoundaryError('初始化期间项目元数据目录发生变化')
        finally:
            os.close(actual)
        yield directory
    finally:
        if lock is not None: os.close(lock)
        if directory is not None: os.close(directory)
        os.close(root_fd)


def recover_link(directory: int) -> None:
    """Finish an interrupted no-clobber publication, removing only same-inode staging links."""
    try:
        final = os.stat('read-policy.json', dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(final.st_mode) or final.st_nlink <= 1:
        return
    for name in os.listdir(directory):
        if not name.startswith(STAGE_PREFIX) or len(name) != len(STAGE_PREFIX) + 32:
            continue
        suffix = name[len(STAGE_PREFIX):]
        if any(ch not in '0123456789abcdef' for ch in suffix): continue
        candidate = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if stat.S_ISREG(candidate.st_mode) and (candidate.st_dev, candidate.st_ino) == (final.st_dev, final.st_ino):
            os.unlink(name, dir_fd=directory)
    # Other hard links are not ours and remain rejected by ReadBoundary.


def publish(directory: int, policy: dict) -> bool:
    """Publish a fully fsynced file without replacing any concurrently created policy."""
    temporary = STAGE_PREFIX + secrets.token_hex(16)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(policy, stream, ensure_ascii=False, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        try:
            os.link(temporary, 'read-policy.json', src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        except FileExistsError:
            return False
        return True
    finally:
        os.unlink(temporary, dir_fd=directory)


def prepare(source: Path, target: Path, initialize: bool = False, authorization: str | None = None) -> tuple[Path, Path]:
    """Preflight both scopes; publish missing policies only with explicit scope authorization."""
    if initialize != bool(authorization and authorization.strip()):
        raise BoundaryError('--init-missing-read-policies 与非空 --authorization-source 必须同时指定；路径不是授权')
    # Existing-policy callers retain the historical root resolution (e.g. macOS /var).
    # Explicit first-time registration uses strict no-link paths and never infers their scope.
    roots = ((canonical_root(source), canonical_root(target)) if initialize else
             (source.expanduser().resolve(strict=True), target.expanduser().resolve(strict=True)))
    source, target = roots
    if source == target or source in target.parents or target in source.parents:
        raise BoundaryError('source and target cannot be equal or nested')
    missing = []
    # No directory creation or policy changes before both policies pass preflight.
    for root in roots:
        try:
            if not inspect(root): missing.append(root)
        except (OSError, ValueError) as error:
            # A prior crash may leave our atomic-publication hard link. Recovery is explicit only.
            if not initialize or not recoverable(root):
                raise BoundaryError(f'{root}: {error}') from error
            missing.append(root)
    if missing and not initialize:
        raise BoundaryError('缺少迁移读取策略: ' + ', '.join(map(str, missing)) +
                            '；已有用户明确授权这两个项目的完整迁移读取时，使用 --init-missing-read-policies --authorization-source "用户授权原意/来源（不含凭据）"；未授权则先确认范围')
    if not missing: return roots
    created = []
    try:
        with ExitStack() as stack:
            descriptors = {root: stack.enter_context(locked_directory(root)) for root in sorted(missing)}
            # Recheck all policies under the acquired locks before first publication.
            for root in roots:
                if root in descriptors: recover_link(descriptors[root])
                inspect(root)
            for root in missing:
                if inspect(root): continue
                ReadBoundary(root, policy_for(root, authorization.strip()))
                if publish(descriptors[root], policy_for(root, authorization.strip())):
                    created.append(str(root / POLICY_PATH))
                require_complete(root)
            for root in roots: require_complete(root)
    except (OSError, ValueError) as error:
        raise BoundaryError(f'迁移策略初始化停止: {error}；本次已创建策略: {created}。未覆盖原文件；修复原因后可用同一授权重试。') from error
    return roots


def recoverable(root: Path) -> bool:
    """Only recognize our narrowly named staging hardlink without reading untrusted content."""
    try:
        directory = secure_fd(root, '.ai-project', directory=True)
        try:
            final = os.stat('read-policy.json', dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(final.st_mode) or final.st_nlink != 2: return False
            for name in os.listdir(directory):
                suffix = name[len(STAGE_PREFIX):] if name.startswith(STAGE_PREFIX) else ''
                if len(suffix) != 32 or any(ch not in '0123456789abcdef' for ch in suffix): continue
                candidate = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISREG(candidate.st_mode) and (candidate.st_dev, candidate.st_ino) == (final.st_dev, final.st_ino): return True
        finally:
            os.close(directory)
    except OSError:
        pass
    return False
