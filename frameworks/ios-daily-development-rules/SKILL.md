---
name: ios-daily-development-rules
description: Maintain existing Objective-C, Swift, UIKit, or SwiftUI projects with risk-based scope and verification. Use for ordinary iOS bugs, features, refactors, testing, and release work; do not use for iOS-to-HarmonyOS migration tasks.
---

# iOS daily development

Use the smallest sufficient context and verification for the actual change. For D0/D1, use the compact fast lane below; do not preload WORKFLOW or quality references. For D2+ or scope expansion, read [templates/WORKFLOW.md](templates/WORKFLOW.md) before changing source. Read [references/verification.md](references/verification.md) only when choosing or expanding validation. Read [references/code-quality-ios.md](references/code-quality-ios.md) only for D2/D3 source work, relevant D4 paths, or an explicit quality/architecture/comment review; D0/D1 use the compact workflow baseline.

- If the project contains `.codex-rules/ios/PROJECT.md`, read it for confirmed project facts.
- For D2+ build/test selection, use `.ai-project/detected-capabilities.json` as a cheap candidate index, but execute only project-confirmed commands; refresh it when build configuration changed.
- If `.ai-project/lifecycle.json` is present, this Skill is primary only for `ios-maintenance`.
- Clear D0/D1 work stays on the fast lane: no simulator, screenshots, new tests, or independent report by default; user performs runtime acceptance.
- For ambiguous, D2+ or dirty-worktree tasks, use the project copy of `.codex-rules/_shared/task_router.py` when present; otherwise use the suite shared router. Exclude pre-existing changes.
- Use `swift-concurrency`, `swift-testing`, or `ios-simulator` only when the task actually needs that narrow capability.
- Treat all Pods and third-party dependency source as read-only. Fix behavior in App-owned code; do not edit dependencies directly or mutate them through `post_install`, automatic patches, or similar mechanisms. If an upstream fix is needed, explain the reason and impact and let the user decide; do not make it unilaterally.
- Lanhu is read-only and scoped to a relevant design gap. Ponytail runs at most once after the final D2/D3 Diff only when complexity signals exist.

- 修改前核对项目路径、仓库/分支、平台和目标身份；冲突暂停。持久任务与恢复按项目 `.codex-rules/_shared/references/task-control.md`；D0/D1 保持轻量，不为流程建候选工程。
- 本次自有代码的每个函数/方法、成员/属性/常量补中文用途与适用契约，重要局部变量/计算与兼容分支解释原因和边界；同步修正失效注释。人工审查语义，排除生成代码及第三方，不用注释行数代替质量。

- 先选择模型/推理等级，再决定平台明确支持的加速；不支持切换或加速时说明未设置并继续当前配置，不得假装成功。

- 项目读取先核对 `.ai-project/read-policy.json`；未知/受限路径禁止打开或遍历。部分范围只允许进程内配置核验，partial 不代表全项目通过；外部编译、测试和旧迁移脚本停止。无策略不得自动全量授权。

- 2.6: keep difficulty, scope and risk independent. A whole-app visual request is broad scope; a short payment/login failure requires scoped risk investigation. Configuration semantics override filename extensions. T4 targeted capability checks do not automatically require a whole-app release. Preserve the D0/D1 fast lane.

## Compact D0/D1 baseline

Confirm the authorized absolute project, platform/target and overlapping user changes from known facts and exact paths. Read the target region and only necessary callers. Preserve state ownership, third-party read-only boundaries and existing user changes; keep Chinese comments accurate for the changed contract. Review the final Diff and references. Run a cheap targeted check only for demonstrated compile/behavior risk. No default whole-tree identity scan, persistent state, extra roles, build, simulator, screenshot, new test or report. Escalate on demonstrated shared state, navigation, persistence, permission, account/payment/security or broader impact. Report the exact minimal checks and any pending runtime acceptance.
