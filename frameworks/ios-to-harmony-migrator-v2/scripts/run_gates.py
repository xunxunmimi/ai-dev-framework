#!/usr/bin/env python3
"""Run only gates selected by a risk profile, with content-fingerprint reuse."""

import argparse
import hashlib
import json
import re
import uuid
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# 完整套件共用身份与证据契约；不创建全局模块或修改系统配置。
import sys
for parent in Path(__file__).resolve().parents:
    shared = parent / "shared/task-routing"
    if (shared / "task_runtime.py").is_file():
        sys.path.insert(0, str(shared))
        break
from read_boundary import ReadBoundary, require_complete, SNAPSHOT_EXCLUDES
from task_runtime import check_operation_hold, atomic_json, binding, digest as content_digest, validate_report, lock, task_dir, read_json



def fingerprint(gate: dict, target: Path) -> str:
    """摘要命令、已确认环境及输入文件；缓存另核对当前任务与完整代码绑定。"""
    identity = {
        "argv": gate.get("argv", []),
        "fingerprintValues": gate.get("fingerprintValues", {}),
        "cacheScope": gate.get('cacheScope'),
        "pythonRuntime": {'version': sys.version, 'executableSha256': hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()} if gate.get('cacheScope') == 'project-only-deterministic' else None,
    }
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode())
    boundary = require_complete(target)
    import fnmatch
    for name in boundary.files(excludes=SNAPSHOT_EXCLUDES):
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in gate.get("inputs", [])):
            digest.update(name.encode())
            digest.update(boundary.read_bytes(name))
    return digest.hexdigest()


def redact(text: str) -> str:
    """过滤常见凭据日志行；保留失败信息，不承诺识别所有秘密。"""
    return re.sub(r"(?im)^.*(?:authorization|token|password|secret|appkey|accesskey).*$", "[REDACTED]", text)


def main() -> int:
    """执行已确认 Profile；输出实际命令、时间和绑定报告，错任务及代码变化不通过。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--profile", choices=("T0", "T1", "T2", "T3", "T4"), required=True)
    parser.add_argument("--task-id")
    parser.add_argument("--slice-id")
    parser.add_argument("--gate", action="append", default=[])
    parser.add_argument("--reuse-valid", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--list", action="store_true")
    parser.add_argument('--dry-run', action='store_true', help='列出命令、确认状态、缓存条件；不建锁、不执行、不写报告')
    parser.add_argument('--purpose', choices=('targeted', 'release'), help='高风险定向检查与发布结论分开；T4 旧配置默认发布')
    args = parser.parse_args()
    target_from_path = args.config.absolute().parent.parent
    if args.config.name != "gate-config.json" or args.config.parent.name != ".migration-v2":
        raise ValueError("Gate 配置必须位于项目 .migration-v2，不能读取未知位置")
    boundary = require_complete(target_from_path)
    config = boundary.read_json(".migration-v2/gate-config.json")
    target = Path(config["targetRoot"]).resolve()
    if target != target_from_path.resolve():
        raise ValueError("Gate 配置目标与读取授权根不一致")
    evidence = Path(config["evidenceRoot"]).resolve()
    cache = target / ".migration-workspace-v2/cache/gates"
    by_id = {gate["id"]: gate for gate in config.get("gates", [])}
    selected = args.gate or config.get("profiles", {}).get(args.profile, [])
    purpose = args.purpose or config.get('profilePurposes', {}).get(args.profile) or ('release' if args.profile == 'T4' else 'targeted')
    if args.list or args.dry_run:
        print(json.dumps({"profile": args.profile, "verificationPurpose": purpose, "selected": selected, 'gates': [by_id.get(x, {'id': x, 'status': 'missing'}) for x in selected], 'writes': [], 'cachePolicy': 'explicit reuse; content/config/toolchain binding; no T4 or release reuse'}, ensure_ascii=False, indent=2))
        return 0
    if not args.task_id:
        raise ValueError("执行 Gate 必须提供 --task-id；先初始化并核对任务身份")
    with lock(task_dir(target, args.task_id) / "verification.lock"):
        workspace = target / ".migration-workspace-v2"
        if not evidence.is_relative_to(workspace / "evidence"):
            raise ValueError("Gate 日志只能写到迁移工作区 evidence 目录")
        if not args.report.resolve().is_relative_to(workspace / "reports"):
            raise ValueError("Gate 报告只能写到迁移工作区 reports 目录，禁止覆盖业务文件")
        if read_json(task_dir(target, args.task_id) / "state.json")["status"] != "active":
            raise ValueError("任务等待权限/阻塞/关闭时不得自动执行 Gate")
        started_at = datetime.now(timezone.utc).isoformat()
        before_binding = binding(target, args.task_id, args.profile, args.slice_id)
        config_sha = content_digest(config)
        plan_path = target / ".migration-workspace-v2/ir/migration-plan.json"
        plan_sha = hashlib.sha256(plan_path.read_bytes()).hexdigest() if plan_path.is_file() else None
        evidence = evidence / args.task_id / uuid.uuid4().hex
        evidence.mkdir(parents=True, exist_ok=True)
        cache.mkdir(parents=True, exist_ok=True)
        if not selected:
            report = {
                "schemaVersion": "2.0",
                "profile": args.profile,
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "status": "blocked",
                "gates": [],
                "reason": "profile has no configured gates",
            }
            args.report.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(args.report, report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1
        results, failed = [], False
        for gate_id in selected:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", gate_id):
                raise ValueError("Gate ID 包含不安全路径字符")
            gate = by_id.get(gate_id)
            if gate is not None:
                check_operation_hold(target, gate)
            if gate is None:
                results.append({"id": gate_id, "status": "blocked", "reason": "gate not configured"})
                failed = True
                continue
            if gate.get("confirmed", False) is not True:
                results.append({"id": gate_id, "status": "blocked", "reason": "gate command/evidence contract is not confirmed"})
                failed = True
                continue
            mark = fingerprint(gate, target)
            cache_file = cache / f"{args.task_id}-{gate_id}.json"
            # 外部 SDK/构建环境没有可证明的完整依赖边界时，不沿用跨进程缓存。
            may_reuse = args.reuse_valid and args.profile != "T4" and purpose != 'release' and gate.get("cacheable") is True and gate.get('cacheScope') == 'project-only-deterministic' and bool(gate.get("fingerprintValues"))
            if may_reuse and cache_file.exists():
                old = json.loads(cache_file.read_text(encoding="utf-8"))
                if old.get("fingerprint") == mark and old.get("status") == "passed" and old.get("binding") == before_binding and old.get("configSha256") == config_sha:
                    results.append({**old, "reused": True})
                    continue
            if gate.get("kind", "command") == "manual":
                evidence_path = Path(str(gate.get("evidence", ""))).expanduser()
                try:
                    relative = evidence_path.relative_to(target).as_posix() if evidence_path.is_absolute() else evidence_path.as_posix()
                    manual = boundary.read_json(relative)
                    status = "passed" if manual.get("status") == "passed" and manual.get("binding") == before_binding and manual.get("reviewer") and manual.get("finishedAt") else "blocked"
                    item = {"id": gate_id, "status": status, "reused": False, "fingerprint": mark, "evidence": str(evidence_path)}
                except (OSError, ValueError, TypeError) as error:
                    item = {"id": gate_id, "status": "blocked", "reused": False, "fingerprint": mark, "reason": str(error)}
                results.append(item)
                failed = failed or item["status"] != "passed"
                continue
            argv = gate.get("argv", [])
            if not argv:
                results.append({"id": gate_id, "status": "blocked", "reason": "command not configured"})
                failed = True
                continue
            log = evidence / f"{gate_id}.log"
            command_started = datetime.now(timezone.utc).isoformat()
            try:
                completed = subprocess.run(
                    argv,
                    cwd=Path(gate.get("cwd", target)),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=int(gate.get("timeoutSeconds", 900)),
                    check=False,
                )
                log.write_text(redact(completed.stdout or ""), encoding="utf-8")
                status = "passed" if completed.returncode == 0 else "failed"
                item = {"id": gate_id, "status": status, "reused": False, "fingerprint": mark, "evidence": str(log)}
            except (OSError, subprocess.TimeoutExpired) as error:
                log.write_text(redact(str(error)) + "\n", encoding="utf-8")
                item = {"id": gate_id, "status": "blocked", "reused": False, "fingerprint": mark, "evidence": str(log)}
            item["binding"] = before_binding
            item["configSha256"] = config_sha
            item["commandRecord"] = {"argv": argv, "cwd": str(Path(gate.get("cwd", target))), "startedAt": command_started, "finishedAt": datetime.now(timezone.utc).isoformat(), "exitCode": completed.returncode if item["status"] != "blocked" else None}
            atomic_json(cache_file, item)
            results.append(item)
            failed = failed or item["status"] != "passed"
        # 执行中源码或配置发生变化，必须拒绝把旧输入的结果记作当前 passed。
        after_binding = binding(target, args.task_id, args.profile, args.slice_id)
        failed = failed or before_binding != after_binding or config_sha != content_digest(json.loads(args.config.read_text()))
        from release_evidence import validate_release
        release_errors = validate_release(config, selected, results, boundary, before_binding) if purpose == 'release' else []
        failed = failed or bool(release_errors)
        report = {
            "schemaVersion": "2.5",
            "binding": before_binding,
            "coverage": before_binding["readCoverage"],
            "limitations": ["excluded credentials are not bound; external commands are not sandboxed by ReadBoundary"],
            "configPath": str(args.config.resolve()),
            "configSha256": config_sha,
            "planSha256": plan_sha,
            "startedAt": started_at,
            "finishedAt": datetime.now(timezone.utc).isoformat(),
            "coverageComplete": set(selected) == set(config.get("profiles", {}).get(args.profile, [])),
            "commands": [item["commandRecord"] for item in results if "commandRecord" in item],
            "profile": args.profile,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "status": "failed" if failed else "passed",
            "verificationPurpose": purpose,
            "releaseContractPassed": purpose == 'release' and not failed,
            "releaseContractErrors": release_errors,
            "gates": results,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(evidence / "report.json", report)
        atomic_json(args.report, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if failed else 0



if __name__ == "__main__":
    raise SystemExit(main())
