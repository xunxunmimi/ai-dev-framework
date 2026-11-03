# AI Dev Kit 使用方法

## 1. 它能做什么

AI Dev Kit 用于给普通新项目或已有项目安装一套可复用的 AI 开发约束和技能：

- `AI Skills`：规划、调试、测试、审查和完成前验证等开发纪律。
- `Grill Me`：在需求和架构不明确时，先盘问清楚再动手。
- `Ponytail`：优先复用现有代码和平台能力，减少无意义代码、依赖和过度设计。

安装后，Codex 和 Antigravity 会从项目中的 `.agents/skills/` 按需读取技能，不需要把所有技能内容塞进每次对话。

## 2. 前置条件

需要 Node.js 18 或更高版本，并保证 `node`、`npx` 可在终端中使用。

安装：

```bash
brew install node
```

检查：

```bash
node --version
npx --version
```

两条命令都显示版本号后再继续。

## 3. 用于新项目

推荐先使用 DevEco Studio、Xcode、Vite 或其他脚手架创建好项目，再安装 AI Dev Kit。部分脚手架不允许在非空目录中创建项目，因此不要把顺序反过来。

项目创建完成后运行：

```bash
cd "<用户目录>/Desktop/MyProject/ai-dev-kit"
./setup-project.sh "/你的项目绝对路径"
```

例如：

```bash
cd "<用户目录>/Desktop/MyProject/ai-dev-kit"
./setup-project.sh "<用户目录>/Desktop/Projects/MyNewApp"
```

注意：`/你的项目绝对路径` 是占位符，必须替换为真实路径。

普通项目安装时应该看到：

```text
Project type: ordinary
```

## 4. 用于已有项目

可以直接把现有项目的根目录传给脚本：

```bash
cd "<用户目录>/Desktop/MyProject/ai-dev-kit"
./setup-project.sh "/已有项目绝对路径"
```

脚本不会删除项目代码。已有 `AGENTS.md` 时只追加一次 AI Dev Kit 入口，不覆盖原有内容；已有同名用户规则时会保留原文件。

首次运行前仍建议提交或备份现有项目，并检查工作树状态。

## 5. 安装后生成的内容

普通项目中会出现：

```text
项目根目录/
├── AGENTS.md
├── AI_WORKFLOW.md
├── CODE_COMMENT_RULES.md
└── .agents/
    ├── skills/
    └── rules/
        ├── ai-development.md
        └── code-comments.md
```

- `AGENTS.md`：Codex 自动读取的项目入口。
- `AI_WORKFLOW.md`：需求澄清、实现和验证的总流程。
- `CODE_COMMENT_RULES.md`：代码注释、公共 API 文档注释和 TODO/FIXME 规范。
- `.agents/skills/`：从三个 GitHub 仓库下载的技能。
- `.agents/rules/`：Antigravity 使用的工作区规则。

注释只用于保存代码本身无法表达清楚的信息：公共契约、非显然约束、状态机、生命周期、平台差异、安全或性能取舍。注释应解释“为什么”和后果，不复述代码；禁止注释掉的旧代码、含糊 TODO、AI 修改记录和敏感信息。修改代码时必须同步检查相关注释。

## 6. 开始开发

安装完成后重启 Codex 或 Antigravity，并在目标项目中开启新对话。

普通开发可以这样说：

```text
请先读取 AGENTS.md 和 AI_WORKFLOW.md，检查适用技能，然后完成这个需求。需求或架构不明确时先使用 grill-me；实现时遵循 Ponytail；结束前运行测试、代码审查和完成前验证。
```

复杂的新项目可以这样说：

```text
先检查当前项目和已有约束，再使用 grill-me 和我确认产品范围、数据模型、架构、异常处理、测试及验收条件。没有达成共同理解前不要实现。确认后按最小完整竖切开发，并在每个阶段运行验证。
```

Codex 中也可以显式指定技能，例如：

```text
使用 $grill-me 盘问这个架构方案。
使用 $code-review 审查当前改动。
使用 $verification-before-completion 完成最终验证。
使用 $ponytail-review 检查是否过度实现。
```

Antigravity 2.0 自带 `/grill-me`。出现同名选择时可优先使用内置 `/grill-me`，其余流程继续遵守 `AI_WORKFLOW.md`。

## 7. 可选安装模式

只生成入口和规则，不下载技能：

```bash
cd "<用户目录>/Desktop/MyProject/ai-dev-kit"
./setup-project.sh "/你的项目绝对路径" --rules-only
```

安装技能，但跳过 Ponytail 原生插件：

```bash
cd "<用户目录>/Desktop/MyProject/ai-dev-kit"
./setup-project.sh "/你的项目绝对路径" --no-plugins
```

完整安装后，在 Codex 中打开 `/hooks`，检查并信任 Ponytail hooks，然后新建对话。

## 8. 更新技能

进入已经安装 AI Dev Kit 的目标项目：

```bash
cd "/你的项目绝对路径"
npx -y skills@latest update --project --yes
```

第三方技能会影响 AI 的操作方式。更新后应检查重要的 `SKILL.md`、脚本和 hooks，再允许其访问敏感项目、密钥或正式环境。

## 9. 常见问题

### 显示 `node: command not found` 或 `npx: command not found`

先运行 `brew install node`，安装结束后关闭并重新打开终端，再检查版本号。

### 可以重复执行安装脚本吗？

可以。脚本不会反复向 `AGENTS.md` 添加相同入口；由 AI Dev Kit 管理的迁移优先级规则可安全更新。

### AI 是否一定自动调用技能？

技能可以根据任务描述自动触发，但关键任务建议在提示词中明确说出技能名称，例如 `$grill-me` 或 `$ponytail-review`。

### 迁移项目也直接这样运行吗？

可以，但应先初始化迁移框架，再运行 AI Dev Kit。具体步骤见《迁移框架与AI-Dev-Kit结合使用方法.md》。

更完整的内部说明见 [`ai-dev-kit/README.md`](../ai-dev-kit/README.md)。
