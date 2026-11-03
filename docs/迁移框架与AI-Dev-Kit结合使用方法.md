# Migration Frameworks 与 AI Dev Kit 结合使用方法

## 1. 为什么要结合使用

两套工具职责不同：

- Migration Frameworks 管理 iOS → HarmonyOS 的源端证据、Migration IR、详细计划、人工计划批准、迁移状态、模型所有权、追踪关系和验收门禁。
- AI Dev Kit 提供通用的需求澄清、规划、调试、测试、代码审查、完成前验证和最小实现约束。

结合使用可以提高开发纪律，但通用技能不能覆盖迁移框架的强制规则。因此 AI Dev Kit 会自动识别迁移项目并写入迁移优先级。

## 2. 强制优先级

迁移项目中的执行优先级为：

```text
用户当前明确要求和平台安全策略
→ .migration-agents 中的迁移控制、批准计划和当前 owner
→ 迁移框架 SKILL.md 与专项规则
→ AI_WORKFLOW.md
→ 通用 AI Skills / Grill Me / Ponytail
```

具体限制：

- Grill Me 可以澄清需求和迁移决策，但不能代替 Migration IR、批准迁移计划或自行开始实施。
- 通用 planning 可以补充迁移计划，但不能绕过计划哈希和人工计划批准。
- Ponytail 可以减少不必要代码，但不能删减源应用行为、平台适配、验证、无障碍、异常处理、测试或证据。
- 通用代码审查与验证只能补充迁移门禁，不能替代迁移门禁。
- 多智能体技能不能覆盖当前 owner、`editableFiles`、`protectedFiles` 或串行编辑规则。
- Antigravity 只能修改迁移工作单允许的 UI 文件；Codex 负责框架指定的编排、契约、逻辑、集成和最终验收。

## 3. 推荐执行顺序

始终推荐：

```text
先初始化迁移框架
→ 再运行 AI Dev Kit
→ 重启 Codex/Antigravity
→ 新建对话并开始只读分析与规划
```

即使顺序反了，当前 AI Dev Kit 也会动态检查 `.migration-agents/AGENTS.md`，但先初始化迁移框架最清晰。

## 4. 第一步：初始化迁移项目

推荐使用双模型版：

```bash
python3 \
  "<用户目录>/Desktop/MyProject/migration-frameworks/ios-to-harmony-dual-model-migrator/scripts/start_migration.py" \
  "/你的iOS源项目绝对路径" \
  "/你的HarmonyOS目标项目绝对路径"
```

如果只使用 Codex，则使用单模型版：

```bash
python3 \
  "<用户目录>/Desktop/MyProject/migration-frameworks/ios-to-harmony-migrator/scripts/start_migration.py" \
  "/你的iOS源项目绝对路径" \
  "/你的HarmonyOS目标项目绝对路径"
```

初始化成功后，目标项目至少应包含：

```text
HarmonyOS目标项目/
├── AGENTS.md
├── .migration-agents/
│   ├── AGENTS.md
│   └── project.json
└── .migration-workspace/
```

## 5. 第二步：安装 AI Dev Kit

把 HarmonyOS 目标项目路径传给 AI Dev Kit：

```bash
cd "<用户目录>/Desktop/MyProject/ai-dev-kit"
./setup-project.sh "/你的HarmonyOS目标项目绝对路径"
```

正确初始化的迁移项目应该显示：

```text
Project type: initialized
```

脚本可能显示四种状态：

| 状态 | 含义 | 应该怎么做 |
|---|---|---|
| `ordinary` | 没检测到迁移结构 | 检查是否把普通项目路径误当成目标路径，或迁移框架尚未运行 |
| `staged` | 迁移框架已放入目标目录，但未初始化 | 先运行迁移框架的 `start_migration.py`，禁止开始业务实现 |
| `partial` | `.migration-agents` 或 `.migration-workspace` 不完整 | 检查初始化错误或使用原迁移框架恢复，不要手工猜状态 |
| `initialized` | 迁移控制中心和工作区完整 | 可以进入只读分析和详细计划阶段 |

## 6. 结合后生成的目录

目标项目最终会类似：

```text
HarmonyOS目标项目/
├── AGENTS.md
├── AI_WORKFLOW.md
├── CODE_COMMENT_RULES.md
├── MIGRATION_PRIORITY.md
├── .agents/
│   ├── skills/
│   └── rules/
│       ├── ai-development.md
│       ├── code-comments.md
│       └── migration-priority.md
├── .migration-agents/
└── .migration-workspace/
```

- `MIGRATION_PRIORITY.md` 规定迁移框架高于通用技能。
- `CODE_COMMENT_RULES.md` 约束通用代码注释；迁移框架内更具体的中文注释规则具有更高优先级，且注释不能代替 Migration IR、追踪矩阵、决策、证据和门禁报告。
- `.agents/rules/migration-priority.md` 让 Antigravity 读取同一优先级。
- 根 `AGENTS.md` 同时指向迁移控制规则和通用工作流。
- AI Dev Kit 重复执行不会重复追加相同入口。

## 7. 第三步：重启并开始新对话

安装完成后：

1. 重启 Codex 或 Antigravity。
2. Codex 中打开 `/hooks`，检查并信任 Ponytail hooks。
3. 在 HarmonyOS 目标项目中新建对话。

给 Codex 的首次提示可以使用：

```text
这是 iOS → HarmonyOS 迁移项目。请先完整读取根 AGENTS.md、MIGRATION_PRIORITY.md、.migration-agents/AGENTS.md、迁移请求、当前检查点和适用的迁移框架 SKILL.md。

源 iOS 项目只读。先扫描和分析源项目，生成 Migration IR、migration-plan.md 和 migration-plan.json，并把范围、架构、切片、风险、阻塞和待决策项返回给我。没有得到我明确回复“确认执行”前，不得生成或修改 HarmonyOS 业务实现，也不得自行批准计划。

通用 AI Skills、Grill Me 和 Ponytail 只能补充迁移流程，不能覆盖迁移计划审批、当前 owner、文件白名单、证据和质量门禁。
```

双模型版给 Antigravity 的提示应使用框架内现成模板：

- [`PROMPT_FOR_ANTIGRAVITY.md`](../migration-frameworks/ios-to-harmony-dual-model-migrator/PROMPT_FOR_ANTIGRAVITY.md)

Antigravity 必须先读取工作单和协作状态，只能在 `editableFiles` 白名单内工作。

## 8. 计划确认和后续实施

Codex 返回详细计划后，你应检查：

- 迁移范围是否完整。
- 目标架构和模块划分是否合理。
- 是否遗漏核心页面、状态、接口、存储、权限或平台能力。
- Codex 与 Antigravity 的 executor 分配是否正确。
- 测试、视觉对照、真机验证和回滚方案是否足够。
- 风险、阻塞和不支持能力是否真实明确。

只有检查通过后回复：

```text
确认执行
```

批准后仍应逐个可验收竖切实施，不要一次迁移整个应用。每个切片完成后立即构建、测试、记录证据并更新断点记忆。

## 9. 已有迁移项目如何补装

已经迁移了一部分的项目也可以补装 AI Dev Kit：

```bash
cd "<用户目录>/Desktop/MyProject/ai-dev-kit"
./setup-project.sh "/已有HarmonyOS迁移项目绝对路径"
```

脚本检测到 `initialized` 后会补充 `MIGRATION_PRIORITY.md` 和 Antigravity 迁移规则，不覆盖 `.migration-agents`、`.migration-workspace` 或产品代码。

补装后必须新建对话，并要求 AI 从当前检查点、批准计划、协作状态和 Git Diff 恢复，不能从旧聊天猜进度。

## 10. 中断或换模型后恢复

首先使用相同源路径和目标路径重新运行迁移框架的 `start_migration.py`，进入 `resume`。然后可再次运行 AI Dev Kit 安装脚本以更新托管规则。

恢复提示：

```text
请按当前迁移框架恢复项目。先读取 AGENTS.md、MIGRATION_PRIORITY.md、.migration-agents/AGENTS.md、CURRENT.md、INDEX.md、覆盖矩阵、批准计划、协作状态和 Git Diff。确认当前 phase、owner 和允许编辑的文件后继续，不要从聊天记录猜测进度，也不要重复实现已验证切片。
```

## 11. 冲突处理

出现以下情况时停止修改并检查：

- AI Dev Kit 显示 `partial`。
- 迁移计划批准哈希失效。
- 通用 skill 建议修改受保护文件。
- Antigravity 需要改变 ViewState、路由、依赖、数据、服务或平台契约。
- 当前 owner 与准备执行的模型不一致。
- 工作树中出现不属于当前任务的修改。
- Ponytail 建议省略迁移范围、测试或验证证据。

不能从控制中心判断冲突时，应把两条冲突规则、相关文件和影响报告给用户，由用户决定；不要静默选一个继续。

## 12. 快速检查清单

开始前：

- [ ] iOS 源项目和 HarmonyOS 目标项目完全分离。
- [ ] 已选择单模型或双模型框架。
- [ ] 迁移框架初始化成功。
- [ ] AI Dev Kit 显示 `Project type: initialized`。
- [ ] 已重启工具并开启新对话。
- [ ] Codex 已读取迁移优先级和控制中心。

实施前：

- [ ] Migration IR 和两份详细计划已生成。
- [ ] 用户已明确回复“确认执行”。
- [ ] 计划批准哈希检查有效。
- [ ] 当前 owner、编辑白名单和保护路径清晰。

完成前：

- [ ] 迁移框架质量门禁通过。
- [ ] 通用测试、代码审查和完成前验证通过。
- [ ] 源到目标追踪完整。
- [ ] 构建日志、测试、截图或真机证据非空且可检查。
- [ ] 阻塞和不支持项已明确记录。
