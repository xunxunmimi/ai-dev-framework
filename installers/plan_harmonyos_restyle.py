#!/usr/bin/env python3
"""独立马甲包单入口的只读启动计划；不安装、不执行制作、不切换日常框架。"""
import argparse
import json
import os
from pathlib import Path
from enable_harmonyos_reimplementation import enable, INSTALL_PARENTS, INSTALL_LOCK
from read_boundary import ReadBoundary
from task_runtime import root_anchor, digest, suite_digest
from project_framework import secure_fd

SUITE = Path(__file__).resolve().parents[1]


def plan(project, source):
    """先复用源/目标和全部安装授权门禁；没有写入或自动批准入口。"""
    installation = enable(project, source, apply=False)
    root, original = project.absolute(), source.absolute()
    boundary = ReadBoundary(root)
    anchors = {'target': root_anchor(root), 'source': root_anchor(original)}
    # 任何已有主框架或项目级指令都需人工审阅；不将其悄悄当作可覆盖空白。
    conflicts = []
    for name in ['.ai-project/framework-installation.json', '.ai-project/lifecycle.json',
                 '.codex-rules', '.migration-v2', 'AGENTS.md', '.ai-project/identity.json',
                 '.ai-project/operation-holds.json', '.ai-project/legacy-baseline.json', '.ai-project/task-input.json']:
        if boundary.exists(name):
            conflicts.append(name)
    for name in ['.ai-project/tasks', '.ai-project/upgrades', '.ai-project/operation-holds', '.ai-project/checkpoints']:
        boundary.require(name)
        try:
            fd = secure_fd(root, name, directory=True)
        except FileNotFoundError:
            continue
        try:
            if os.listdir(fd): conflicts.append(name)
        finally:
            os.close(fd)
    # apply 的父目录必须是安全真实目录，而不只是拥有名字权限。
    for name in INSTALL_PARENTS:
        boundary.require(name)
        try:
            fd = secure_fd(root, name, directory=True)
        except FileNotFoundError:
            continue
        os.close(fd)
    if boundary.exists(INSTALL_LOCK):
        boundary.read_bytes(INSTALL_LOCK)  # 拒绝链接、硬链接与特殊文件；不申请或创建锁。
    if anchors != {'target': root_anchor(root), 'source': root_anchor(original)}:
        raise ValueError('计划期间源或目标目录身份改变')
    if installation['selection']['targetPolicySha256'] != ReadBoundary(root).sha256 or installation['selection']['sourcePolicySha256'] != ReadBoundary(original).sha256:
        raise ValueError('计划期间源或目标策略变化')
    result = {'status': 'blocked-existing-context' if conflicts else 'preflight-only',
        'phase': 'rebrand-production-before-acceptance', 'primaryWorkflow': 'harmonyos-app-reimplementation',
        'dailyFrameworkBeforeAcceptance': False, 'automaticDailyHandoff': False,
        'executionSupported': False, 'writes': [], 'projectAnchors': anchors,
        'conflicts': conflicts, 'installationPreview': installation,
        'suiteVersion': (SUITE/'VERSION').read_text().strip(), 'suiteSha256': suite_digest(SUITE),
        'nextStep': ('保留既有规则、任务和授权；需要正式审阅模式交接，不能覆盖或卸载。' if conflicts else
                     '只读预检不执行安装。使用 installers/bootstrap_harmonyos_restyle.py 复核计划并显式安装独立 Skill；制作验收后另行明确接入日常框架。')}
    result['planSha256'] = digest(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = plan(args.project, args.source)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result['conflicts'] else 0
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({'status':'blocked','reason':str(error)}, ensure_ascii=False)); return 2

if __name__ == '__main__': raise SystemExit(main())
