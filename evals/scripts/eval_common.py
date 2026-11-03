#!/usr/bin/env python3
"""Shared deterministic helpers for the framework evaluation harness."""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


EVAL_ROOT = Path(__file__).resolve().parents[1]
SUITE_ROOT = EVAL_ROOT.parent
CASE_FILES = (EVAL_ROOT / "cases" / "quick.json", EVAL_ROOT / "cases" / "real.json")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in CASE_FILES:
        value = read_json(path)
        if not isinstance(value, list):
            raise ValueError(f"{path} 必须是 JSON 数组")
        cases.extend(value)
    return cases


def find_case(case_id: str) -> dict[str, Any]:
    matches = [case for case in load_cases() if case["id"] == case_id]
    if len(matches) != 1:
        raise ValueError(f"用例 {case_id!r} 应唯一存在，实际 {len(matches)} 个")
    return matches[0]


def validate_cases() -> list[str]:
    cases = load_cases()
    errors: list[str] = []
    ids = [case.get("id") for case in cases]
    duplicate_ids = sorted(key for key, count in Counter(ids).items() if count > 1)
    if duplicate_ids:
        errors.append(f"重复用例 ID: {', '.join(duplicate_ids)}")

    expected_counts = {"quick": 12, "real": 24}
    expected_per_framework = {"quick": 4, "real": 8}
    frameworks = {
        "ios-to-harmony-migrator-v2",
        "ios-daily-development-rules",
        "harmonyos-daily-development-rules",
    }
    for suite, count in expected_counts.items():
        suite_cases = [case for case in cases if case.get("suite") == suite]
        if len(suite_cases) != count:
            errors.append(f"{suite} 应有 {count} 个用例，实际 {len(suite_cases)} 个")
        counts = Counter(case.get("framework") for case in suite_cases)
        for framework in frameworks:
            actual = counts.get(framework, 0)
            if actual != expected_per_framework[suite]:
                errors.append(
                    f"{suite}/{framework} 应有 {expected_per_framework[suite]} 个用例，实际 {actual} 个"
                )

    for case in cases:
        missing = [key for key in ("id", "suite", "framework", "prompt", "expected") if key not in case]
        if missing:
            errors.append(f"{case.get('id', '<unknown>')} 缺字段: {', '.join(missing)}")
            continue
        expected = case["expected"]
        if not expected.get("levels"):
            errors.append(f"{case['id']} 缺 expected.levels")
        if expected.get("framework") not in frameworks:
            errors.append(f"{case['id']} 的 expected.framework 无效")
        if case["suite"] == "quick":
            fixture = EVAL_ROOT / "fixtures" / case.get("fixture", "")
            if not fixture.is_dir():
                errors.append(f"{case['id']} 的 fixture 不存在: {fixture}")
    return errors


def render(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format_map(variables)
    if isinstance(value, list):
        return [render(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: render(item, variables) for key, item in value.items()}
    return value


def iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from iter_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_dicts(item)


def _token_usage(candidate: dict[str, Any]) -> dict[str, int] | None:
    aliases = {
        "inputTokens": ("input_tokens", "inputTokens", "prompt_tokens"),
        "cachedInputTokens": ("cached_input_tokens", "cachedInputTokens"),
        "outputTokens": ("output_tokens", "outputTokens", "completion_tokens"),
        "reasoningTokens": ("reasoning_tokens", "reasoningTokens", "reasoning_output_tokens"),
        "totalTokens": ("total_tokens", "totalTokens"),
    }
    result: dict[str, int] = {}
    for output_key, keys in aliases.items():
        for key in keys:
            value = candidate.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                result[output_key] = value
                break
    if not result:
        return None
    if "totalTokens" not in result and {"inputTokens", "outputTokens"} <= result.keys():
        result["totalTokens"] = result["inputTokens"] + result["outputTokens"]
    if "inputTokens" in result:
        result["uncachedInputTokens"] = max(0, result["inputTokens"] - result.get("cachedInputTokens", 0))
    return result


def _command_text(candidate: dict[str, Any]) -> str | None:
    for key in ("command", "cmd"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value:
            return " ".join(str(item) for item in value)
    return None


def parse_trace(path: Path) -> dict[str, Any]:
    commands: list[str] = []
    seen_commands: set[str] = set()
    usage: dict[str, int] | None = None
    events = 0
    parse_errors = 0
    if not path.exists():
        return {"events": 0, "parseErrors": 0, "commands": [], "tokenUsage": None}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        events += 1
        for candidate in iter_dicts(event):
            maybe_usage = _token_usage(candidate)
            if maybe_usage is not None:
                usage = maybe_usage
            command = _command_text(candidate)
            if command:
                identity = str(candidate.get("id") or candidate.get("call_id") or command)
                if identity not in seen_commands:
                    seen_commands.add(identity)
                    commands.append(command)
    return {"events": events, "parseErrors": parse_errors, "commands": commands, "tokenUsage": usage}


def git_changed_files(repository: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    chunks = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    files: list[str] = []
    index = 0
    while index < len(chunks):
        entry = chunks[index]
        index += 1
        if not entry:
            continue
        status = entry[:2]
        path = entry[3:]
        if path:
            files.append(path)
        if "R" in status or "C" in status:
            if index < len(chunks) and chunks[index]:
                files.append(chunks[index])
                index += 1
    return sorted(set(files))


def list_files(repository: Path) -> list[str]:
    return sorted(
        path.relative_to(repository).as_posix()
        for path in repository.rglob("*")
        if path.is_file() and ".git" not in path.parts
    )


def git_diff(repository: Path) -> str:
    tracked = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--binary", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    notes = "".join(f"\n# UNTRACKED: {path}\n" for path in untracked)
    return tracked + notes


def evaluate_content_checks(case: dict[str, Any], repository: Path) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    root = repository.resolve()
    for index, requirement in enumerate(case["expected"].get("contentChecks", []), start=1):
        relative = requirement["path"]
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            outcomes.append({"id": index, "path": relative, "pass": False, "details": "file missing or outside workspace"})
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        missing = [text for text in requirement.get("contains", []) if text not in content]
        present = [text for text in requirement.get("notContains", []) if text in content]
        outcomes.append({
            "id": index,
            "path": relative,
            "pass": not missing and not present,
            "details": f"missing={missing}, forbiddenPresent={present}",
        })
    return outcomes


def glob_match(path: str, patterns: Iterable[str]) -> bool:
    pure = PurePosixPath(path)
    for pattern in patterns:
        if pattern == "**/*":
            return True
        if fnmatch.fnmatchcase(path, pattern) or pure.match(pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:]):
            return True
    return False


def grade(case: dict[str, Any], run: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or read_json(EVAL_ROOT / "config.json")
    expected = case["expected"]
    result = run.get("result") or {}
    changed = sorted(set(run.get("changedFiles") or []))
    files = sorted(set(run.get("files") or []))
    commands = run.get("trace", {}).get("commands") or []
    patterns = config["commandPatterns"]

    counts = {
        "build": sum(bool(re.search(patterns["build"], command)) for command in commands),
        "test": sum(bool(re.search(patterns["test"], command)) for command in commands),
        "simulator": sum(bool(re.search(patterns["simulator"], command)) for command in commands),
    }
    report_paths = [path for path in changed if re.search(patterns["reportPath"], path)]
    allowed = expected.get("allowedChangedFiles")
    forbidden = expected.get("forbiddenChangedFiles", [])
    unrelated = [] if allowed is None else [path for path in changed if not glob_match(path, allowed)]
    forbidden_changed = [path for path in changed if glob_match(path, forbidden)]
    required_missing = [path for path in expected.get("requiredArtifacts", []) if not glob_match_any(files, path)]
    forbidden_present = [path for path in expected.get("forbiddenArtifacts", []) if glob_match_any(files, path)]

    checks: list[dict[str, Any]] = []

    def check(check_id: str, passed: bool, details: str, critical: bool = False) -> None:
        checks.append({"id": check_id, "pass": bool(passed), "critical": critical, "details": details})

    check("framework", result.get("framework") == expected["framework"], f"actual={result.get('framework')}, expected={expected['framework']}", True)
    check("task-level", result.get("taskLevel") in expected["levels"], f"actual={result.get('taskLevel')}, expected={expected['levels']}", True)
    check("runtime-acceptance", not expected.get("runtimeAcceptance") or result.get("runtimeAcceptance") in expected["runtimeAcceptance"], f"actual={result.get('runtimeAcceptance')}, expected={expected.get('runtimeAcceptance', 'any')}")
    check("changed-files-present", bool(changed) or expected.get("allowNoChanges", False), f"changed={len(changed)}")
    check("allowed-scope", not unrelated, f"unrelated={unrelated}", True)
    check("forbidden-scope", not forbidden_changed, f"forbidden={forbidden_changed}", True)
    check("required-artifacts", not required_missing, f"missing={required_missing}", True)
    check("forbidden-artifacts", not forbidden_present, f"present={forbidden_present}", True)
    for outcome in run.get("contentChecks", []):
        check(f"content-{outcome['id']}", outcome["pass"], f"{outcome['path']}: {outcome['details']}", True)
    if expected.get("contentChecks") and len(run.get("contentChecks", [])) != len(expected["contentChecks"]):
        check("content-check-completeness", False, "content check results missing", True)
    reported = sorted(set(result.get("changedFiles") or [])) if isinstance(result, dict) else []
    target_relative = sorted(path.removeprefix("harmony-target/") for path in changed)
    reported_matches = reported == changed or reported == target_relative
    check("reported-changed-files", reported_matches, f"reported={reported}, actual={changed}")
    for kind, expected_key in (("build", "maxBuildCommands"), ("test", "maxTestCommands"), ("simulator", "maxSimulatorCommands")):
        maximum = expected.get(expected_key)
        check(f"{kind}-budget", maximum is None or counts[kind] <= maximum, f"actual={counts[kind]}, max={maximum}")
    report_max = expected.get("maxReportFiles")
    check("report-budget", report_max is None or len(report_paths) <= report_max, f"actual={len(report_paths)}, max={report_max}")
    check("codex-exit", run.get("exitCode") == 0, f"exit={run.get('exitCode')}", True)

    passed_count = sum(item["pass"] for item in checks)
    critical_failures = [item["id"] for item in checks if item["critical"] and not item["pass"]]
    usage = run.get("trace", {}).get("tokenUsage")
    return {
        "schemaVersion": "1.0",
        "caseId": case["id"],
        "suite": case["suite"],
        "framework": case["framework"],
        "suiteIdentity": run.get('suiteIdentity'),
        "comparisonContract": run.get('comparisonContract'),
        "pass": passed_count == len(checks),
        "deterministicScore": round(100 * passed_count / len(checks), 2),
        "checks": checks,
        "criticalFailures": critical_failures,
        "metrics": {
            "changedFiles": len(changed),
            "unrelatedChangedFiles": len(unrelated),
            "buildCommands": counts["build"],
            "testCommands": counts["test"],
            "simulatorCommands": counts["simulator"],
            "reportFiles": len(report_paths),
            "durationSeconds": run.get("durationSeconds"),
            "tokenUsageAvailable": usage is not None,
            "tokenUsage": usage,
        },
        "quality": run.get("quality"),
    }


def glob_match_any(paths: Iterable[str], pattern: str) -> bool:
    return any(glob_match(path, [pattern]) for path in paths)
