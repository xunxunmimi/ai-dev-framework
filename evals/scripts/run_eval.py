#!/usr/bin/env python3
"""Run isolated Codex evaluations and score them deterministically."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import hashlib
import platform
from datetime import datetime, timezone
from pathlib import Path

from eval_common import EVAL_ROOT, SUITE_ROOT, evaluate_content_checks, find_case, git_changed_files, git_diff, grade, list_files, load_cases, read_json, render, validate_cases, write_json


def suite_identity() -> dict:
    """绑定实际执行套件，版本参数不能仅作标签；不扫描用户目录。"""
    sys.path.insert(0, str(SUITE_ROOT / 'shared/task-routing'))
    from task_runtime import suite_digest
    return {'version': (SUITE_ROOT / 'VERSION').read_text().strip(), 'contentSha256': suite_digest(SUITE_ROOT)}


def prepare_fixture(case: dict, workspace: Path) -> dict:
    """复制合成夹具、显式授权其读取并执行真实接入；不调用模型或业务工具。"""
    shutil.copytree(EVAL_ROOT / 'fixtures' / case['fixture'], workspace, dirs_exist_ok=True)
    roots = [workspace / 'ios-source', workspace / 'harmony-target'] if case['fixture'] == 'migration' else [workspace]
    for root in roots:
        write_json(root / '.ai-project/read-policy.json', {'schemaVersion': '1.0', 'confirmed': True, 'scopeKind': 'project', 'projectRoot': str(root.resolve()), 'mode': 'complete', 'allow': ['**'], 'deny': [], 'scopeDescription': 'eval-owned synthetic fixture only; never grants a business project access'})
    initialize_repository(workspace)
    rendered = render(case, {'suite': str(SUITE_ROOT), 'workspace': str(workspace)})
    for command in rendered.get('setup', []):
        argv = command.get('argv')
        if not isinstance(argv, list) or not argv:
            raise ValueError('setup.argv must be a nonempty array')
        run_checked([str(item) for item in argv], workspace)
    commit_setup(workspace)
    return rendered


def run_checked(argv: list[str], cwd: Path) -> None:
    subprocess.run(argv, cwd=cwd, check=True, stdin=subprocess.DEVNULL, capture_output=True, text=True)


def initialize_repository(workspace: Path) -> None:
    run_checked(["git", "init", "-q"], workspace)
    run_checked(["git", "config", "user.name", "Framework Eval"], workspace)
    run_checked(["git", "config", "user.email", "eval@localhost"], workspace)
    run_checked(["git", "add", "-A"], workspace)
    run_checked(["git", "commit", "-q", "-m", "fixture baseline"], workspace)


def commit_setup(workspace: Path) -> None:
    run_checked(["git", "add", "-A"], workspace)
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=workspace)
    if result.returncode != 0:
        run_checked(["git", "commit", "-q", "-m", "framework setup baseline"], workspace)


def codex_version(codex: str) -> str:
    result = subprocess.run([codex, "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return (result.stdout or result.stderr).strip()


def artifact_directory(version: str, case_id: str, explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = EVAL_ROOT / "artifacts" / "runs" / version / f"{stamp}-{case_id}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def run_case(args: argparse.Namespace) -> Path:
    case = find_case(args.case)
    if case["suite"] != "quick":
        raise ValueError("real 用例必须先冻结独立 worktree；当前 runner 只自动执行 quick fixture")
    identity = suite_identity()
    if args.framework_version != identity['version']:
        raise ValueError(f"requested version {args.framework_version} differs from executing suite {identity['version']}; run that version's own runner")
    if args.dry_run:
        print(json.dumps({'status': 'dry-run', 'caseId': case['id'], 'suiteIdentity': identity, 'setup': case.get('setup', []), 'modelInvocation': False, 'notice': 'use fixture-check for a zero-model setup rehearsal'}, ensure_ascii=False, indent=2))
        return None
    codex = args.codex or shutil.which("codex")
    if not codex:
        raise RuntimeError("未找到 codex CLI；请用 --codex 指定绝对路径")
    output = artifact_directory(args.framework_version, case["id"], args.output)
    workspace = Path(tempfile.mkdtemp(prefix=f"framework-eval-{case['id']}-"))
    try:
        rendered_case = prepare_fixture(case, workspace)
        working_directory = (workspace / rendered_case.get("workdir", ".")).resolve()
        if not working_directory.is_relative_to(workspace.resolve()):
            raise ValueError("workdir 不得逃逸隔离工作区")

        trace_path = output / "trace.jsonl"
        stderr_path = output / "stderr.log"
        result_path = output / "agent-result.json"
        command = [
            codex,
            "exec",
            "--json",
            "--ephemeral",
            "--approve-for-me",
            "-C",
            str(working_directory),
            "--output-schema",
            str(EVAL_ROOT / "schemas" / "agent-result.schema.json"),
            "-o",
            str(result_path),
        ]
        if args.model:
            command.extend(["--model", args.model])
        if args.reasoning:
            command.extend(["-c", f'model_reasoning_effort="{args.reasoning}"'])
        command.append(rendered_case["prompt"])
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        timed_out = False
        try:
            with trace_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open("w", encoding="utf-8") as stderr_file:
                completed = subprocess.run(
                    command,
                    cwd=working_directory,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=args.timeout,
                )
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = 124
        duration = round(time.monotonic() - started, 3)

        try:
            result = read_json(result_path)
        except (FileNotFoundError, json.JSONDecodeError):
            result = None
        from eval_common import parse_trace

        run_record = {
            "schemaVersion": "1.0",
            "caseId": case["id"],
            "frameworkVersion": args.framework_version,
            "suiteIdentity": identity,
            "comparisonContract": {'caseSha256': hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest(), 'fixtureSha256': fixture_digest(case['fixture']), 'environment': platform.platform(), 'python': platform.python_version(), 'model': args.model, 'reasoning': args.reasoning, 'codexVersion': codex_version(codex)},
            "model": args.model,
            "reasoning": args.reasoning,
            "codexVersion": codex_version(codex),
            "startedAt": started_at,
            "durationSeconds": duration,
            "exitCode": exit_code,
            "timedOut": timed_out,
            "result": result,
            "changedFiles": git_changed_files(workspace),
            "files": list_files(workspace),
            "contentChecks": evaluate_content_checks(case, workspace),
            "trace": parse_trace(trace_path),
        }
        (output / "changes.diff").write_text(git_diff(workspace), encoding="utf-8")
        write_json(output / "case.rendered.json", rendered_case)
        write_json(output / "run.json", run_record)
        write_json(output / "score.json", grade(case, run_record))
        if args.keep_workspace:
            destination = output / "workspace"
            shutil.copytree(workspace, destination)
        return output
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def command_validate(_: argparse.Namespace) -> int:
    errors = validate_cases()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("OK: 12 quick + 24 real，三个框架配比正确，fixture 可用")
    return 0


def command_list(args: argparse.Namespace) -> int:
    for case in load_cases():
        if args.suite and case["suite"] != args.suite:
            continue
        print(f"{case['id']}\t{case['suite']}\t{case['framework']}")
    return 0


def command_run(args: argparse.Namespace) -> int:
    output = run_case(args)
    if output is None:
        return 0
    score = read_json(output / "score.json")
    print(f"{score['caseId']}: {'PASS' if score['pass'] else 'FAIL'} ({score['deterministicScore']})")
    print(output)
    return 0 if score["pass"] else 2


def command_run_suite(args: argparse.Namespace) -> int:
    if not args.execute_all:
        print("拒绝执行：批量运行会消耗模型 Token；请显式添加 --execute-all", file=sys.stderr)
        return 2
    selected = [case for case in load_cases() if case["suite"] == args.suite]
    failures = 0
    for case in selected:
        child = argparse.Namespace(**vars(args))
        child.case = case["id"]
        child.output = None
        try:
            output = run_case(child)
            if output is None:
                continue
            score = read_json(output / "score.json")
            print(f"{case['id']}: {'PASS' if score['pass'] else 'FAIL'}")
            failures += not score["pass"]
        except Exception as error:  # keep suite evidence when one case fails
            failures += 1
            print(f"{case['id']}: ERROR {error}", file=sys.stderr)
    return 0 if failures == 0 else 2


def command_grade(args: argparse.Namespace) -> int:
    run_path = Path(args.run).expanduser().resolve()
    run = read_json(run_path)
    case = find_case(run["caseId"])
    score = grade(case, run)
    destination = run_path.with_name("score.json")
    write_json(destination, score)
    print(destination)
    return 0 if score["pass"] else 2


def add_execution_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--framework-version", required=True)
    parser.add_argument("--model")
    parser.add_argument("--reasoning", choices=("low", "medium", "high", "xhigh"))
    parser.add_argument("--codex", help="codex CLI 绝对路径；默认从 PATH 查找")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--keep-workspace", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--dry-run", action="store_true", help="只审查实际版本和调用计划，零模型调用")


def fixture_digest(name: str) -> str:
    """按相对路径和内容摘要固定合成输入，不含动态授权的绝对路径。"""
    root = EVAL_ROOT / 'fixtures' / name
    values = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*')) if p.is_file() and not p.is_symlink()}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def command_fixture_check(args: argparse.Namespace) -> int:
    """真正演练所有 quick setup，暴露仅校验 JSON 无法发现的漂移。"""
    results = []
    for case in load_cases():
        if case['suite'] != 'quick' or (args.case and case['id'] != args.case):
            continue
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix='framework-fixture-check-') as temporary:
                prepare_fixture(case, Path(temporary))
            results.append({'caseId': case['id'], 'status': 'passed', 'seconds': round(time.monotonic() - started, 4)})
        except Exception as error:
            results.append({'caseId': case['id'], 'status': 'failed', 'error': str(error)})
    report = {'status': 'passed' if results and all(x['status'] == 'passed' for x in results) else 'failed', 'modelCalls': 0, 'tokenUsage': {'available': False, 'reason': 'no model invoked'}, 'scope': 'synthetic setup only; does not prove business or agent quality', 'results': results}
    if args.output:
        write_json(Path(args.output), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'passed' else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate", help="零模型调用：校验基准集")
    validate_parser.set_defaults(function=command_validate)
    fixture_parser = subparsers.add_parser('fixture-check', help='零模型：真实演练隔离夹具接入')
    fixture_parser.add_argument('--case')
    fixture_parser.add_argument('--output')
    fixture_parser.set_defaults(function=command_fixture_check)
    list_parser = subparsers.add_parser("list", help="零模型调用：列出用例")
    list_parser.add_argument("--suite", choices=("quick", "real"))
    list_parser.set_defaults(function=command_list)
    run_parser = subparsers.add_parser("run", help="执行一个 quick 用例")
    run_parser.add_argument("--case", required=True)
    add_execution_arguments(run_parser)
    run_parser.set_defaults(function=command_run)
    suite_parser = subparsers.add_parser("run-suite", help="显式批量执行 quick 用例")
    suite_parser.add_argument("--suite", choices=("quick",), required=True)
    suite_parser.add_argument("--execute-all", action="store_true")
    add_execution_arguments(suite_parser)
    suite_parser.set_defaults(function=command_run_suite)
    grade_parser = subparsers.add_parser("grade", help="零模型调用：重新评分已有 run.json")
    grade_parser.add_argument("--run", required=True)
    grade_parser.set_defaults(function=command_grade)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        return args.function(args)
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
