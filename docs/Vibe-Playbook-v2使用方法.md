# Vibe Playbook v2 使用方法

## 1. 它能做什么

Vibe Playbook v2 用于把一个 App 想法从 0 推进到可运行、经过验证、具备发布条件的版本，而不是只产出调研报告、需求文档或空项目。

完整流程为：

```text
产品想法
→ 验证问题与 MVP 范围
→ 用户体验与技术架构
→ 垂直切片计划
→ 功能实现与验证
→ Release Candidate
→ 发布后的数据和迭代
```

它适合：

- 从一个模糊想法开始创建新的 iOS、Android、Web、桌面或跨平台 App。
- 已有产品需求，希望规划并完成第一个可运行 MVP。
- 已经开发了一部分，需要从项目状态继续推进到可发布版本。
- 不熟悉完整产品开发流程，希望由 AI 判断当前阶段和下一步。

它不适合接管普通的小功能修改、日常 Bug 修复或 iOS → HarmonyOS 迁移。普通开发使用 AI Dev Kit；迁移项目使用 Migration Frameworks。

## 2. 项目级隔离

Vibe Playbook v2 不安装到全局目录，只复制到指定 App 项目：

```text
你的App项目/
└── .agents/
    ├── skills/
    │   └── vibe-playbook-v2/
    └── rules/
        └── vibe-playbook-v2.md
```

这意味着：

- 只在当前 App 项目中生效。
- 不写入 `~/.codex/skills`、`~/.claude/skills` 等全局目录。
- 不安装全局插件或 hooks。
- 不同项目可以拥有各自独立的进度、产品范围和技术决策。
- 没有安装 Vibe Playbook v2 的项目不会读取它。

源工具目录：

```text
<用户目录>/Desktop/MyProject/vibe-playbook-v2/
```

这个目录只是工具源，不会自动对所有项目生效。

## 3. 三种执行模式

### Prototype

适合验证产品需求或技术可行性：

- 允许使用临时数据、简化界面或一次性实现。
- 重点是尽快验证关键假设。
- 不默认满足正式发布的工程质量。

提示词示例：

```text
使用 $vibe-playbook-v2，以 prototype 模式验证这个 AI 菜谱 App 是否值得继续做。
```

### MVP（默认）

适合完成第一个可交付版本：

- 至少完成一条端到端核心用户流程。
- 包含必要的错误处理、测试、隐私和无障碍基础。
- 不加入与核心价值无关的未来功能。

提示词示例：

```text
使用 $vibe-playbook-v2，以 MVP 模式从 0 到 1 做一个 iOS 习惯追踪 App。
```

### Release

适合把已有 MVP 推进到发布候选版本：

- 检查正式环境、签名、权限、升级、隐私、可访问性和发布配置。
- 准备部署或 App Store 所需材料和验证清单。
- 发布到生产环境或提交商店前仍会要求你明确确认。

提示词示例：

```text
使用 $vibe-playbook-v2，把当前 Web App 推进到 release candidate，但未经我确认不要部署生产环境。
```

## 4. 安装到新项目

首先为 App 准备一个独立项目目录。不要使用 `<用户目录>/Desktop/MyProject/` 作为目标，因为这是多个框架和项目的集合目录。

例如：

```bash
mkdir -p "<用户目录>/Desktop/Projects/HabitFlow"
```

安装 Vibe Playbook v2：

```bash
cd "<用户目录>/Desktop/MyProject/vibe-playbook-v2"
./setup-project.sh "<用户目录>/Desktop/Projects/HabitFlow"
```

其中 `<用户目录>/Desktop/Projects/HabitFlow` 必须替换成你的真实 App 项目路径。

安装成功后会显示：

```text
Vibe Playbook v2 is active only in this project.
```

Vibe Playbook v2 的安装器只复制本地文件，不需要 Node.js、`npm` 或 `npx`。

## 5. 安装到已有项目

把已有项目的根目录传给安装脚本：

```bash
cd "<用户目录>/Desktop/MyProject/vibe-playbook-v2"
./setup-project.sh "/你的已有App项目绝对路径"
```

脚本会：

1. 把主 skill 复制到项目的 `.agents/skills/vibe-playbook-v2/`。
2. 创建或补充根 `AGENTS.md`。
3. 创建 Antigravity 项目规则。
4. 保留已有项目代码和用户自己维护的同名规则。
5. 遇到非 v2 管理的同名 skill 时停止，不强制覆盖。

已有项目首次使用时，要求 AI 先检查代码、文档、Git 状态、已有功能和验证证据，再判断从哪个阶段继续，不能假设项目仍处于第 0 阶段。

## 6. 安装后如何启动

安装完成后：

1. 把 Codex 或 Antigravity 的工作区打开到目标 App 项目。
2. 不要把工作区打开到 `<用户目录>/Desktop/MyProject/` 多项目集合目录。
3. 新建对话，明确调用 `$vibe-playbook-v2`。

完整的新项目提示词：

```text
使用 $vibe-playbook-v2，从 0 到 1 做一个 iOS 习惯追踪 App。

目标用户是经常忘记坚持习惯的年轻上班族。先检查当前项目，再帮我确认核心问题、MVP 范围和最短用户流程。事实由你调查，需要我做决定时给出推荐方案。确认 MVP 后继续完成架构、实现、测试和代码审查，目标是得到可以在真机运行的 release candidate。未经我明确确认，不要提交 App Store、购买服务或修改生产环境。
```

需求还很模糊时：

```text
使用 $vibe-playbook-v2。我想做一个帮助用户整理照片的 App，但还不确定核心差异化。先用 prototype 模式调查和验证最值得测试的核心假设，不要一开始就搭完整后台或订阅系统。
```

已经有清晰需求时：

```text
使用 $vibe-playbook-v2。产品需求已经写在 docs/product-spec.md。先核查需求和现有代码；如果信息足够就跳过通用调研，直接确认 MVP 边界、制定垂直切片并开始实现。
```

## 7. 工作流如何运行

### 阶段 0：确定产品问题

AI 会确认：

- 目标用户是谁。
- 用户在什么情况下遇到什么问题。
- App 承诺提供什么结果。
- 支持的平台和现实约束。
- 什么结果可以证明第一版值得继续。
- 哪些内容明确不在第一版范围内。

能从代码、市场、官方文档和工具中查到的事实由 AI 自己调查；只有真正需要你选择的产品决策才会提问，并附推荐答案。

### 阶段 1：验证并确定 MVP

AI 会把调研结果区分为事实、观察、推断和假设，不会编造市场规模、收入、转化率或用户需求。

这一阶段应得到：

- 一条完整的核心用户流程。
- MVP 包含和不包含的功能。
- 可观察的验收条件。
- 成功指标及验证方式。
- 数据、隐私、变现、无障碍和平台限制。

如果你已经提供了可信调研和清晰需求，允许跳过重复调研。

### 阶段 2：体验与架构

AI 会设计从首次启动到获得核心价值的最短路径，并考虑加载、空状态、错误、权限拒绝、重试和恢复等真实状态。

技术选择优先顺序为：

```text
项目已有技术和模式
→ 平台原生能力
→ 已安装依赖
→ 小型成熟依赖
→ 确有必要时才自建基础设施
```

不会因为“以后可能需要”就默认加入后台、登录、订阅、广告、分析 SDK、跨平台框架或复杂架构。

### 阶段 3：垂直切片计划

开发计划按用户可以实际体验的完整小闭环拆分，而不是先写完所有模型、服务，再集中做页面。

每个切片必须说明：

- 用户最终能完成什么。
- 预计修改哪些边界或文件。
- 验收条件。
- 自动验证和人工验证方式。
- 依赖、风险和恢复方式。

第一个切片应尽早证明核心流程可以端到端运行。

### 阶段 4：实现与验证

AI 可以连续完成安全、可逆并且需求明确的实现，不需要每修改一个文件都停下来询问。

每个有效切片完成后应：

1. 运行聚焦测试。
2. 运行 lint、类型检查和构建等项目检查。
3. 尽可能实际操作用户流程。
4. 进行正确性代码审查。
5. 修复确认的问题并重新验证。
6. 更新项目进度和下一步。

只有编译成功不能证明功能正确；只有单元测试也不能证明端到端流程可用。

### 阶段 5：发布候选版本

AI 会检查适用的：

- 全新安装和版本升级。
- 正式环境配置和密钥管理。
- 权限、隐私、账号和数据删除。
- 支付恢复或数据迁移。
- 无障碍、设备、浏览器、语言和网络条件。
- 崩溃监控、日志、备份、回滚和支持路径。
- 签名、版本号、构建产物和发布材料。

AI 可以准备发布候选版本，但以下动作必须由你明确授权：

- 部署生产环境。
- 提交 App Store 或其他应用商店。
- 购买服务或修改价格、订阅和域名。
- 联系测试人员、用户、审核人员或合作方。
- 执行破坏性的生产数据迁移。

## 8. 项目进度文件

首次正式启动后，Vibe Playbook v2 会在目标项目中维护：

```text
docs/vibe/
├── status.md
├── product-brief.md
└── decisions.md
```

- `status.md`：当前阶段、证据、阻塞和唯一下一步。
- `product-brief.md`：目标用户、产品承诺、核心流程、MVP 范围和验收条件。
- `decisions.md`：只记录以后修改成本较高的产品和架构决策。

这些文件属于当前项目，不会和其他 App 共用。

## 9. 跨会话继续

重新打开项目后使用：

```text
使用 $vibe-playbook-v2。读取 docs/vibe/status.md、product-brief.md、decisions.md、当前代码和验证结果，核对实际进度后继续下一步。不要只根据旧聊天猜测状态。
```

如果文档与代码不一致，以当前代码、可运行行为和验证证据为准，并同步修正 `status.md`。

## 10. 与 AI Dev Kit 配合

Vibe Playbook v2 和 AI Dev Kit 可以安装在同一个 App 项目中，职责不同：

```text
Vibe Playbook v2
负责判断 0→1 当前阶段、产品范围和下一项交付

AI Dev Kit
负责 brainstorming、planning、测试、调试、代码审查和完成前验证等工程纪律
```

推荐安装顺序：

```bash
# 1. 安装 Vibe Playbook v2
cd "<用户目录>/Desktop/MyProject/vibe-playbook-v2"
./setup-project.sh "/你的App项目绝对路径"

# 2. 可选：安装 AI Dev Kit
cd "<用户目录>/Desktop/MyProject/ai-dev-kit"
./setup-project.sh "/你的App项目绝对路径" --profile web
```

非 Web 项目可以省略 `--profile web`，使用默认 Core；系统开发可使用 `--profile systems`。

两者结合时：

- Vibe Playbook v2 负责编排产品阶段。
- AI Dev Kit 中匹配当前任务的 skill 执行具体工程工作。
- 不会默认同时运行所有已安装 skill。
- `brainstorming` 用于模糊需求；`grilling` 只用于压力测试已有方案。
- `code-review` 负责正确性审查；`ponytail-review` 只能额外检查过度设计。

Vibe Playbook v2 本身不需要 Node.js；只有安装 AI Dev Kit 的第三方 skill 时需要 `node` 和 `npx`。

## 11. 与迁移框架的边界

Vibe Playbook v2 主要用于新 App 的 0→1 开发，不替代 iOS → HarmonyOS 迁移框架。

如果项目中存在：

```text
.migration-agents/
.migration-workspace/
MIGRATION_PRIORITY.md
```

执行优先级为：

```text
用户当前明确要求和平台安全策略
→ Migration Frameworks 的控制、批准计划和当前 owner
→ 项目其他规则
→ Vibe Playbook v2
```

Vibe Playbook v2 不得绕过迁移计划批准、文件白名单、当前 owner、证据或质量门禁。

## 12. 更新 Vibe Playbook v2

工具源更新后，对需要更新的 App 项目重新运行安装命令：

```bash
cd "<用户目录>/Desktop/MyProject/vibe-playbook-v2"
./setup-project.sh "/你的App项目绝对路径"
```

脚本会更新项目中由 v2 管理的 skill 和 Antigravity 规则，但不会覆盖：

- 项目业务代码。
- `docs/vibe/` 中的项目状态和决策。
- 用户自己维护的同名 skill 或规则。
- `AGENTS.md` 中已有的其他项目说明。

更新后重新打开 Codex 或 Antigravity，并新建对话。

## 13. 常见问题

### 会不会和其他项目的 skill 冲突？

不会。它只存在于被安装项目的 `.agents/skills/`，其他项目看不到。

### 一个项目内安装很多 skill 会不会都同时运行？

不会。Vibe Playbook v2 只在新 App 0→1、继续现有 playbook 或明确调用 `$vibe-playbook-v2` 时触发；普通小功能和 Bug 修复不会由它接管。

### 是否必须做完整市场调研？

不必须。如果已有可信证据和明确产品范围，可以走快速路径。调研的目的只是避免做错产品，不是增加流程。

### 是否每一步都会停下来问我？

不会。安全、可逆、意图清晰的调研、实现和验证可以连续推进。MVP 边界、隐私/支付等高成本选择及正式发布动作才需要你确认。

### 是否一定能自动发布 App？

它可以把项目推进到 release candidate 并准备发布材料，但正式部署、商店提交、付费和外部沟通必须得到你的明确授权。

### 安装时需要 `npx` 吗？

不需要。Vibe Playbook v2 安装器只复制本地项目文件。

### 可以重复执行安装脚本吗？

可以。由 v2 管理的副本会安全更新，`AGENTS.md` 不会反复追加相同入口，项目进度不会被重置。

更完整的源说明见：

```text
<用户目录>/Desktop/MyProject/vibe-playbook-v2/README.md
```
