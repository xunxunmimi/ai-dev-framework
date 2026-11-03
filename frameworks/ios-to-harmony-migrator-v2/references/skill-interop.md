# 外部 Skill 协作

Codex 会根据 Skill 的名称和 description 自动判断是否适用；用户也可以用 `$skill-name` 显式调用。安装 Skill 不代表每次都会读取其全文，但触发描述过宽或彼此重叠会增加误触发和上下文。

## 接入要求

- 只安装当前工作流需要的单个 Skill，不安装大合集。
- 审查 `SKILL.md`、脚本、Hook、网络访问、写入位置、许可证和更新方式。
- 禁止自动修改 SDK、全局配置或依赖源；禁止内部 Registry 和未固定的远程执行。
- 描述必须窄；不要让“修改任何 .ets 文件”同时触发多个长 Skill。
- 优先保留确定性脚本，删除重复的通用工作流说明。
- 第三方版本记录仓库、提交哈希和审查日期；更新后重新审查。

## 使用建议

- 日常 Swift/iOS：系统只在请求命中 Swift 并发、测试或模拟器任务时自动使用相应 Skill；需要确定调用时写 `$swift-concurrency` 等。
- HarmonyOS：ArkTS 静态检查和 ETS LSP 仅在编译诊断或明确检查时调用；普通迁移仍由本框架负责路由。
- 如果多个 Skill 同时适用，以迁移框架决定范围，以语言/工具 Skill 提供窄能力，不让外部 Skill 扩大回归范围。
- 蓝湖 MCP 是设计事实读取工具，不是迁移编排器。按 `task-context.json.toolRouting.lanhu` 使用；只启用读取需求、设计、区域和素材的工具，凭据留在本机，缓存不得进入项目仓库。
- `ponytail-review` 是实现后的可选复杂度审查。仅在 `toolRouting.ponytailReview` 允许且最终 Diff 出现规则定义的信号时调用一次；不要安装或启用 Ponytail 的常驻模式与生命周期 Hook。

## 当前试点版本

- `lanhu-mcp`: `dsphper/lanhu-mcp@1ec250f10f14a42418f4673f17f08f185f45b9bd`，本地 stdio，只读工具白名单。
- `ponytail-review`: `DietrichGebert/ponytail@e3ba2aa6f1e6f0bc4d69eb09c9f0d0a93af56156` 的 `skills/ponytail-review`，不包含插件 Hook。
