# {{PROJECT_NAME}} 迁移控制

先读取 `.ai-project/lifecycle.json` 判断主框架。`migration-active` 使用本框架；`migration-transition` 只有迁移等价、缺失 iOS 行为和迁移证据任务使用本框架，普通鸿蒙 Bug/功能由 HarmonyOS 日常规则负责。每个任务只选一个主框架。

修改迁移项目时读取 `.migration-v2/task-context.json` 的 `mustRead`，不加载 `doNotReadByDefault`；同一任务中未变化的内容不重复读取。明确的 T0/T1 可直接走快速通道，不为了分级生成报告；范围不清或 T2+ 时使用三阶段分类和任务基线。

新迁移或新增未批准范围先完成 `.migration-workspace-v2/ir/discovery-baseline.json`，运行 Phase 0 发现门禁，再生成并批准绑定哈希的迁移计划。门禁失败时不得修改 HarmonyOS 业务源码。既有已批准切片和迁移过渡期普通维护不倒退重做；源证据变化时只补受影响区域。

所有任务遵守框架 `references/core-contract.md`。由分类器根据请求和代码事实自动确定 T0–T4，用户无需声明大小；从最小充分验证开始，只在依赖、失败或不确定性出现时升级。T0/T1 默认不启动模拟器、不做 UI 自动化/截图、不新增测试、不生成独立报告，运行验收交给用户。普通 Bug 不自动重跑完整迁移、设备或全量回归。

项目事实集中在 `.migration-v2/project.json`，当前状态集中在 `.migration-v2/CURRENT.md`。不要在本文件叠加覆盖规则；项目策略直接修改机器可读字段，并保留决策原因。

遵循 `.migration-v2/task-context.json` 的 `toolRouting`。蓝湖只在存在已配置设计源或本次明确链接且任务需要设计证据时按范围读取；`ponytail-review` 仅在 T2/T3 最终 Diff 出现复杂度信号时最多运行一次。工具不可用不得扩大任务或阻塞普通开发。

只对实际验证范围使用 `verified`。发布、支付、身份、安全存储和数据迁移使用 T4。
