#!/usr/bin/env python3
"""安装前确认读取授权；只初始化策略，不推导或放宽业务源码权限。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'shared/task-routing'))
from read_boundary import ReadBoundary, POLICY_PATH, bootstrap_bytes, BoundaryError
from task_runtime import atomic_json
from project_framework import MANAGED_MAPPINGS


def bootstrap(project: Path, platform: str, source: Path | None = None) -> str:
    """校验必要配置的权限和链接，再写固定策略；已有策略不同则拒绝覆盖。"""
    root = project.resolve(strict=True)
    incoming = json.loads(bootstrap_bytes(source.parent.resolve(), source.name)) if source else None
    if incoming and incoming.get('scopeKind') != 'project':
        raise BoundaryError('新传入策略必须明确为 project 长期范围；临时任务范围使用 init --task-read-policy')
    boundary = ReadBoundary(root, incoming)
    # 只允许调用者明确授权的配置路径；不打开任何业务源码或未知旧规则。
    framework = 'ios-daily-development-rules' if platform == 'ios' else 'harmonyos-daily-development-rules'
    names = [name for name, _ in MANAGED_MAPPINGS[framework]]
    names += [POLICY_PATH, 'AGENTS.md', f'.codex-rules/{platform}/PROJECT.md', '.ai-project/lifecycle.json', '.ai-project/evidence-index.md', '.ai-project/detected-capabilities.json', '.ai-project/framework-installation.json']
    for name in names:
        boundary.exists(name)
    if source:
        if boundary.exists(POLICY_PATH):
            if boundary.read_json(POLICY_PATH) != incoming:
                raise BoundaryError('已有读取策略不同，禁止安装器自动覆盖授权边界')
        else:
            atomic_json(root / POLICY_PATH, incoming)
    return boundary.policy['mode']


def main() -> int:
    """输出读取模式供安装器选择安全分支；任何权限错误在复制规则前失败。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--platform', choices=('ios', 'harmonyos'), required=True)
    parser.add_argument('--policy', type=Path)
    args = parser.parse_args()
    try:
        print(bootstrap(args.project, args.platform, args.policy))
        return 0
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({'status': 'blocked', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
