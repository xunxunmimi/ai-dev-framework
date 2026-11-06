#!/usr/bin/env python3
"""Shared, evidence-based task router for the three development frameworks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Iterable

from task_runtime import atomic_json, lock, safe_path, task_dir, confirmed_identity, task_identity, protocol_gate, project_lock, validate_state, task_record, project_relative_paths, read_json, tree_snapshot
from read_boundary import ReadBoundary, task_read_scope
from contextlib import nullcontext


DOC_SUFFIXES = {".md", ".txt", ".rst", ".adoc"}
META_SUFFIXES = {".json", ".json5", ".yaml", ".yml", ".toml"}
VISUAL_WORDS = ("文案", "拼写", "颜色", "字体", "字号", "间距", "圆角", "图标", "切图", "布局", "样式", "视觉", "design", "layout", "spacing", "font", "color", "copy text")
FEATURE_WORDS = ("新增", "开发", "实现", "添加功能", "new feature", "new page", "implement", "add feature")
MIGRATION_WORDS = (
    "迁移", "等价", "parity", "migration", "ios行为缺失", "源端行为", "迁移矩阵",
    "与ios不一致", "和ios不一致", "苹果端不一致", "双端不一致", "对齐ios", "原ios行为", "源项目行为",
    "ios一致性", "ios流程一致", "ios行为一致", "ios还原", "苹果端一致性", "源项目一致性",
)
TRANSITION_PARITY_SIGNALS = tuple(word for word in MIGRATION_WORDS if word not in {"迁移", "等价", "migration"}) + (
    "迁移等价", "迁移证据", "遗漏迁移", "ios事实源", "原项目行为缺失",
    "migration parity", "parity residual", "missing ios behavior", "migration evidence",
)
NEGATED_PARITY_SIGNALS = (
    "不涉及ios一致", "不需要ios一致", "无需ios一致", "不做ios对齐", "无需ios对齐",
    "不涉及ios等价", "不是迁移等价", "不涉及迁移等价", "仅普通鸿蒙问题",
)
BATCH_PARITY_WORDS = ("多处", "多项", "多条", "好几处", "好几项", "一批", "若干", "批量")
BATCH_PARITY_PATTERN = re.compile(r"(?:共|有)?(?:[2-9]|10|[两二三四五六七八九十]+)(?:个|处|项|条)")
CROSS_WORDS = ("公共接口", "公共基类", "跨模块", "全局重构", "新依赖", "构建系统", "public api", "cross-module", "dependency")
CONTROL_PREFIXES = (".ai-project/", ".codex-rules/", ".migration-v2/", ".migration-workspace-v2/", ".migration-agents/", ".migration-workspace/")


def run_git(project: Path, *args: str) -> str:
    """只读查询 Git；失败返回空串，不修改工作区。"""
    if ReadBoundary(project).policy["mode"] != "complete":
        return ""
    result = subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True, check=False)
    return result.stdout if result.returncode == 0 else ""


def status_paths(project: Path) -> list[str]:
    # -z 的重命名包含第二个路径字段，不能对每段机械裁掉三个字符。
    """读取含未跟踪与重命名的真实路径；返回相对路径列表。"""
    chunks = run_git(project, "status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0")
    paths, position = [], 0
    while position < len(chunks):
        entry = chunks[position]
        position += 1
        if not entry:
            continue
        paths.append(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:
            if position < len(chunks) and chunks[position]:
                paths.append(chunks[position])
                position += 1
    return project_relative_paths(project, paths)


def digest(path: Path, project: Path) -> str | None:
    """摘要已授权文件；链接、未知路径或权限错误阻塞，真实缺失返回 None。"""
    boundary = ReadBoundary(project)
    relative = path.relative_to(project).as_posix()
    return hashlib.sha256(boundary.read_bytes(relative)).hexdigest() if boundary.exists(relative) else None


def snapshot(project: Path) -> dict:
    """记录当前脏文件与项目根，作为分级基线。"""
    boundary = ReadBoundary(project)
    if boundary.policy["mode"] == "partial":
        return {"schemaVersion": "1.1", "projectRoot": str(project.resolve()), "paths": tree_snapshot(project), "readCoverage": boundary.coverage()}
    paths = status_paths(project)
    return {
        "schemaVersion": "1.0",
        "projectRoot": str(project.resolve()),
        "paths": {path: digest(project / path, project) for path in paths},
    }


def task_files(project: Path, baseline: dict | None, explicit: Iterable[str]) -> tuple[list[str], list[str]]:
    """比较本任务文件与基线并返回旧改动排除项；跨项目基线抛错。"""
    if baseline and (not baseline.get("projectRoot") or Path(baseline["projectRoot"]).resolve() != project.resolve()):
        raise ValueError("baseline 属于其他项目，暂停核对身份")
    explicit_files = sorted({str(Path(item)) for item in explicit if item})
    for item in explicit_files:
        ReadBoundary(project).require(item)
        safe_path(project, item)
    if explicit_files:
        before = set((baseline or {}).get("paths", {}))
        return explicit_files, sorted(before - set(explicit_files))
    current = snapshot(project)
    if not baseline:
        return [], sorted(current["paths"])
    before = baseline.get("paths", {})
    changed = [path for path, value in current["paths"].items() if path not in before or before.get(path) != value]
    removed = [path for path, value in before.items() if value is not None and not ReadBoundary(project).exists(path)]
    selected = sorted(set(changed + removed))
    return selected, sorted(set(before) - set(selected))


def contains(value: str, words: Iterable[str]) -> bool:
    """判断文本是否包含候选词，不产生副作用。"""
    return any(word in value for word in words)


def compact_text(value: str) -> str:
    """小写并去空白，统一中文/英文路由匹配。"""
    return "".join(value.lower().split())


def is_transition_parity_request(request: str) -> bool:
    """识别过渡期等价请求并优先处理明确否定。"""
    compact = compact_text(request)
    if contains(compact, NEGATED_PARITY_SIGNALS):
        return False
    return contains(compact, TRANSITION_PARITY_SIGNALS)


def is_batch_parity_request(request: str) -> bool:
    """判断是否为需联合诊断的批量一致性问题。"""
    compact = compact_text(request)
    return is_transition_parity_request(request) and (
        contains(compact, BATCH_PARITY_WORDS) or BATCH_PARITY_PATTERN.search(compact) is not None
    )


def infer_intent(request: str, mode: str) -> str:
    """按请求与模式推导初始意图；仍需 Agent 结合代码复核。"""
    value = request.lower()
    compact = compact_text(request)
    if mode == "migration" and not contains(compact, NEGATED_PARITY_SIGNALS) and contains(compact, MIGRATION_WORDS):
        return "migration"
    if contains(value, ("发布版本", "提交审核", "准备上线", "打包上线", "release build", "app store", "上架")):
        return "release"
    if contains(value, ("解释", "分析原因", "为什么", "说明", "explain", "diagnose")):
        return "investigation"
    if contains(value, ("重构", "refactor")):
        return "refactor"
    if contains(value, FEATURE_WORDS):
        return "feature"
    if contains(value, VISUAL_WORDS):
        return "ui-adjustment"
    if contains(value, ("修复", "问题", "bug", "崩溃", "报错", "异常", "fix", "crash", "error")):
        return "bugfix"
    if contains(value, ("文档", "注释", "readme", "documentation")):
        return "documentation"
    return "change"


def infer_risks(request: str, files: list[str]) -> list[str]:
    """识别行为风险；数据破坏与普通视觉修改采用不同分支。"""
    value = re.sub(r"(?:不涉及|不修改|无需修改|不处理)(?:支付|账号|登录|签名|数据迁移)", "", request.lower())
    risks: set[str] = set()
    if contains(value, VISUAL_WORDS):
        risks.add("ui-local")
    mapping = {
        "navigation": ("导航", "路由", "返回栈", "deeplink", "deep link", "navigation", "routing"),
        "state": ("共享状态", "状态恢复", "状态机", "state restoration", "state machine"),
        "persistence": ("持久化", "数据库", "缓存格式", "userdefaults", "coredata", "preferences"),
        "concurrency": ("并发", "线程", "actor", "sendable", "async", "race condition"),
        "permission": ("权限流程", "授权流程", "隐私权限", "permission flow", "privacy permission"),
        "public-api": ("公共接口", "公共基类", "public api", "shared contract"),
        "dependency": ("新增依赖", "升级依赖", "替换 sdk", "dependency", "package upgrade"),
        "data-migration": ("数据库迁移", "数据迁移", "schema migration"),
        "security": ("安全存储", "密钥", "证书", "验签", "keychain", "keystore", "signature verification"),
        "account": ("账号绑定", "账号注销", "登录态", "身份认证", "account binding", "authentication"),
    }
    for flag, words in mapping.items():
        if contains(value, words):
            risks.add(flag)

    # 数据破坏与视觉词分开判断，避免“删除按钮颜色”误升高风险。
    destructive = contains(value, ("永久删除", "删除照片", "删除数据", "删除历史", "清空数据库", "清空记录", "批量删除", "擦除数据", "delete data", "delete photos", "drop table", "wipe data"))
    negated_delete = contains(value, ("不删除数据", "不删除照片", "不涉及数据删除", "无需删除数据"))
    visual_only = contains(value, VISUAL_WORDS) and not contains(value, ("逻辑", "流程", "回调", "永久", "清空", "擦除", "实现", "功能", "跳转", "状态", "校验", "logic", "flow"))
    if destructive and not negated_delete and not visual_only:
        risks.add("data-destruction")
    payment_subject = contains(value, ("支付", "内购", "订单", "交易", "payment", "storekit", "purchase"))
    payment_behavior = contains(value, ("逻辑", "回调", "验签", "下单", "退款", "恢复购买", "收据", "流程", "callback", "receipt", "refund", "restore"))
    if payment_subject and (payment_behavior or not visual_only):
        risks.add("payment")
    if contains(value, ("登录", "登入", "认证", "账号", "login", "sign in", "authentication")) and not visual_only:
        risks.add("account")
    if contains(value, ("签名", "profile", "provisioning", "signing")) and not visual_only:
        risks.add("signing")

    if contains(value, ("发布版本", "提交审核", "准备上线", "打包上线", "签名发布", "release build", "上架")):
        risks.add("release")

    lowered_files = " ".join(files).lower()
    if contains(lowered_files, ("package.swift", "podfile", "oh-package", "build-profile", "hvigorfile")):
        risks.add("dependency")
    if any(Path(name).name.lower() in {"module.json5", "privacyinfo.xcprivacy"} or Path(name).suffix.lower() in {".entitlements", ".xcprivacy"} for name in files):
        risks.add("permission-config")
    if any(Path(name).suffix.lower() in {".pbxproj", ".xcconfig", ".xcscheme"} for name in files):
        risks.add("build-config")
    if contains(value, ("隐私清单", "权限配置", "网络权限", "privacy manifest", "permission config")):
        risks.add("permission-config")
    # 控制脚本和授权/门禁规则也可能影响安全，不能因位于隐藏目录就归 D0。
    if any(Path(name).name in {"gate-config.json", "read-policy.json", "verification-commands.json", "task_router.py", "read_boundary.py", "task_runtime.py"} for name in files):
        risks.add("verification-contract")
    return sorted(risks)


def verification_requirements(risks: list[str], number: int, global_scope: bool) -> dict:
    """按能力选择检查；高风险局部任务不自动变成整 App 发布。"""
    required = ["diff"]
    if number == 0:
        required.append("format-or-schema")
    elif number >= 2:
        required.extend(["affected-build-or-static-check", "affected-tests-or-behavior-evidence"])
    for risk, check in {"payment": "payment-failure-recovery-idempotency", "account": "account-state-failure-recovery", "security": "security-boundary", "data-migration": "data-upgrade-interruption-rollback", "data-destruction": "destructive-data-recovery", "permission-config": "permission-manifest-and-denial", "signing": "signed-artifact-identity-profile", "verification-contract": "negative-controls-and-rollback"}.items():
        if risk in risks:
            required.append(check)
    release = "release" in risks
    if release:
        required.extend(["release-build", "final-artifact-environment-signature", "related-regression", "applicable-real-device"])
    return {"required": required, "scope": "release-related" if release else "affected-flows" if global_scope or number >= 3 else "task-local", "releaseBuildRequired": release or "signing" in risks, "realDevice": "required-where-applicable" if release else "only-for-device-dependent-evidence", "wholeAppRegression": release, "missingEvidence": "unverified; never infer passed from a lower profile"}


def module_count(files: list[str]) -> int:
    """计算顶层目录数作为范围线索，不等同实际架构模块。"""
    modules = set()
    for value in files:
        parts = Path(value).parts
        if parts:
            modules.add(parts[0])
    return len(modules)


def is_control_file(value: str) -> bool:
    """识别框架控制文件，避免控制元数据抬高业务改动等级。"""
    normalized = value.replace("\\", "/")
    return normalized == "AGENTS.md" or normalized == ".gitignore" or normalized.startswith(CONTROL_PREFIXES)


def classify(request: str, files: list[str], mode: str = "daily", stage: str = "pre") -> dict:
    """结合意图、风险和文件范围返回分级与验证预算；不执行验证。"""
    intent = infer_intent(request, mode)
    batch_parity = mode == "migration" and intent == "migration" and is_batch_parity_request(request)
    product_files = [path for path in files if not is_control_file(path)]
    risks = infer_risks(request, files)
    modules = module_count(product_files)
    prefix = "T" if mode == "migration" else "D"
    high = {"payment", "account", "security", "signing", "data-migration", "data-destruction", "release"}
    global_scope = contains(compact_text(request), ("全app", "全应用", "所有页面", "整个应用", "全局样式", "wholeapp", "allpages", "app-wide"))
    cross_scope = contains(request.lower(), CROSS_WORDS) or len(product_files) > 8 or modules >= 3

    if high.intersection(risks):
        number, reason = 4, "high-risk identity, payment, data, security, or release behavior"
    elif "verification-contract" in risks:
        number, reason = 3, "authorization, routing, or verification contract changed"
    elif files and not product_files:
        number, reason = 0, "non-executable framework or task metadata only"
    elif mode == "migration" and intent == "migration":
        number = 3
        reason = "batch migration parity diagnosis before implementation slices" if batch_parity else "migration parity slice"
    elif global_scope or {"public-api", "dependency"}.intersection(risks) or contains(request.lower(), CROSS_WORDS) or len(product_files) > 8 or modules >= 3:
        number, reason = 3, "shared contract, dependency, or demonstrated cross-module scope"
    elif intent in {"feature", "refactor"} or {"navigation", "state", "persistence", "concurrency", "permission", "permission-config", "build-config"}.intersection(risks) or len(product_files) > 3:
        number, reason = 2, "feature or behavior scope requiring targeted runtime judgment"
    elif intent in {"documentation", "investigation"} and not files:
        number, reason = 0, "read-only or documentation request"
    elif files and all(Path(path).suffix.lower() in DOC_SUFFIXES | META_SUFFIXES for path in files):
        number, reason = 0, "task-scoped files are documentation or metadata only"
    else:
        number, reason = 1, "localized change with no demonstrated cross-cutting risk"

    confidence = "high" if stage != "pre" and files else "medium"
    if number in {0, 1} and not files and intent == "change":
        confidence = "low"
    level = f"{prefix}{number}"
    # 三个维度独立保留；旧 impactLevel 作为兼容路由结果，不等于工作量。
    difficulty = "complex" if cross_scope or intent == "refactor" or {"concurrency", "state", "public-api"}.intersection(risks) else "moderate" if intent == "feature" or number >= 2 else "simple"
    return {
        "intent": intent,
        "impactLevel": level,
        "reason": reason,
        "riskFlags": risks,
        "confidence": confidence,
        "difficulty": difficulty,
        "riskLevel": "high" if high.intersection(risks) else "behavior" if risks and risks != ["ui-local"] else "low",
        "scopeLevel": "whole-app" if global_scope else "cross-module" if cross_scope else "feature" if number in {2,3} else "local",
        "verificationRequirements": verification_requirements(risks, number, global_scope),
        "classificationStatus": "provisional-until-code-reviewed",
        "executionPolicy": {"defaultAgents": 1, "parallel": "only-if-user-or-project-authorized; independent non-overlapping slices; coordination cost justified", "escalate": ["demonstrated scope/risk expansion", "two failures without progress", "contradictory evidence"], "deescalate": "document evidence that scope is local and risk absent", "stop": ["identity/scope conflict", "denied operation", "stale evidence", "missing required external evidence"]},
        "scope": {
            "taskFiles": files,
            "fileCount": len(files),
            "productFileCount": len(product_files),
            "controlFileCount": len(files) - len(product_files),
            "moduleCount": modules,
        },
        "contextBudget": {
            "maxPrimaryFiles": [4, 8, 16, 28, 40][number],
            "maxInitialBytes": [6000, 12000, 24000, 40000, 60000][number],
            "budgetKind": "soft-byte-budget-not-token-measurement",
            "history": "off" if number <= 1 else "summary-only",
            "rawLogs": "pointer-only",
        },
        "verificationPlan": [
            "format-or-schema",
            "diff-and-optional-cheap-compile",
            "targeted-build-tests",
            "cross-module-contracts",
            "release-and-real-device-boundaries",
        ][number],
        "modelAdvice": {"capability": "strong" if number >= 3 else "balanced" if number == 2 else "cost-efficient", "reasoning": "high" if number >= 3 else "low", "applied": False, "acceleration": "not-set; choose model first; platform support must be confirmed"},
        "taskShape": "parity-batch" if batch_parity else "single-scope",
        "executionStrategy": "joint-diagnosis-then-slices" if batch_parity else "direct-scoped-work",
        "runRuntimeByDefault": number >= 2,
        "independentReport": number >= 3,
    }


def build_context(request: str, project: Path, mode: str, stage: str, files: list[str], excluded: list[str]) -> dict:
    """生成软预算下的必读/按需列表，保留旧改动排除指针。"""
    result = classify(request, files, mode=mode, stage=stage)
    excluded_preview = excluded[:20]
    if len(excluded) > 20:
        excluded_preview.append(f"... {len(excluded) - 20} additional pre-existing paths omitted")
    # 预算仅约束初始读取清单；超大文件放入按需列表，不能用字符数冒充 Token。
    selected, deferred, initial_bytes = [], [], 0
    for name in files:
        ReadBoundary(project).require(name)
        path = safe_path(project, name)
        size = len(ReadBoundary(project).read_bytes(name)) if ReadBoundary(project).exists(name) else 0
        if len(selected) < result["contextBudget"]["maxPrimaryFiles"] and initial_bytes + size <= result["contextBudget"]["maxInitialBytes"]:
            selected.append(name)
            initial_bytes += size
        else:
            deferred.append(name)
    result.update({
        "schemaVersion": "2.1",
        "stage": stage,
        "projectRoot": str(project.resolve()),
        "request": request,
        "excludedPreexistingChangeCount": len(excluded),
        "excludedPreexistingChanges": excluded_preview,
        "mustRead": selected,
        "readIfNeeded": deferred,
        "doNotReadByDefault": ["historical reports", "raw logs", "unaffected modules", "full migration matrix"],
        "externalEvidence": [],
    })
    return result


def main() -> int:
    """解析分级命令，隔离任务上下文并原子保存；不会运行构建。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path)
    parser.add_argument('--request-only', action='store_true', help='仅按请求/声明路径输出初步计划；不打开、枚举或写入项目')
    parser.add_argument("--request", required=True)
    parser.add_argument("--mode", choices=("daily", "migration"), default="daily")
    parser.add_argument("--platform", choices=("ios", "harmonyos", "migration"), default="ios")
    parser.add_argument("--stage", choices=("pre", "post", "final"), default="pre")
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--task-id", help="持久任务唯一标识；先用 task_runtime init 建立范围与身份")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--write-context", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="只输出路由和审查计划，不写基线或任务上下文")
    args = parser.parse_args()

    if args.request_only:
        if args.write_baseline or args.write_context or args.task_id or args.baseline:
            parser.error('request-only cannot create/read task state or baselines')
        result = classify(args.request, args.file, args.mode, args.stage)
        result.update(scopeEvidence='request-and-declared-paths-only; files not inspected', confidence='medium', projectReads=0, writes=[])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.project is None:
        parser.error('--project is required unless --request-only')

    project = args.project.expanduser().resolve()
    # macOS 的 /var 和 /private/var 根别名先按用户给定根映射；不 resolve 叶子或项目内链接。
    for option in ("baseline", "write_context"):
        value = getattr(args, option)
        if value is not None:
            try:
                relative = value.absolute().relative_to(args.project.expanduser().absolute())
            except ValueError:
                relative = value.absolute().relative_to(project)
            setattr(args, option, project / relative)
    with task_read_scope(project, args.task_id) if args.task_id else nullcontext():
        protocol_gate(project)
        if args.task_id:
            # 每任务独立槽位；任务已开始后禁止用 pre 重写不可变基线。
            directory = task_dir(project, args.task_id)
            state = task_record(project, directory / "state.json")
            if state['status'] in {'closed', 'cancelled'}:
                raise ValueError('终结任务不能再次写入路由上下文')
            identity = task_identity(project, state)
            if args.platform not in {identity["platform"], "migration"}:
                raise ValueError("路由 platform 与已确认项目身份冲突")
            directory = task_dir(project, args.task_id)
            state = read_json(directory / "state.json", project)
            if args.write_baseline:
                raise ValueError("task-id 模式基线由 init 创建，不能覆盖")
            baseline = {"projectRoot": str(project.resolve()), "paths": state["baseline"]}
            args.write_context = None if args.dry_run else directory / "context.json"
        else:
            baseline = read_json(args.baseline, project) if args.baseline and ReadBoundary(project).exists(args.baseline.relative_to(project).as_posix()) else None
            if baseline and (not baseline.get("projectRoot") or Path(baseline["projectRoot"]).resolve() != project.resolve()):
                raise ValueError("旧基线属于其他项目，暂停身份核对")
            if args.baseline and args.write_baseline and not args.baseline.resolve().is_relative_to((project / ".ai-project").resolve()):
                raise ValueError("基线只能写入项目 .ai-project")
            if args.stage == "pre" and args.write_baseline and args.baseline and not args.dry_run:
                # 新任务开始使用刚捕获的快照，绝不使用磁盘上遗留的上一任务基线。
                baseline = snapshot(project)
                with project_lock(project, '.ai-project/tasks.lock'):
                    protocol_gate(project)
                    atomic_json(args.baseline, baseline)
        for output in (args.baseline if args.write_baseline else None, args.write_context):
            if output and not output.resolve().is_relative_to((project / ".ai-project").resolve()):
                raise ValueError("基线/上下文只能写入项目 .ai-project，不能覆盖业务或依赖文件")
        files, excluded = task_files(project, baseline, args.file)
        context = build_context(args.request, project, args.mode, args.stage, files, excluded)
        context["readCoverage"] = ReadBoundary(project).coverage()
        context["platform"] = args.platform
        context["taskId"] = args.task_id
        if args.write_context and not args.dry_run:
            with project_lock(project, '.ai-project/tasks.lock'):
                if args.task_id:
                    latest = task_record(project, directory / 'state.json')
                    if latest != state or latest['status'] != 'active':
                        raise ValueError('生成上下文期间任务变化，禁止终态回写')
                    task_identity(project, latest)
                atomic_json(args.write_context, context)
        print(json.dumps(context, ensure_ascii=False, indent=2))
        return 0



if __name__ == "__main__":
    raise SystemExit(main())
