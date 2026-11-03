# 项目生命周期与框架切换

- `ios-maintenance`：iOS 日常规则是唯一主框架。
- `migration-active`：迁移 V2 是唯一主框架。
- `migration-transition`：普通 HarmonyOS Bug、功能、构建和上线准备由 HarmonyOS 日常规则负责；迁移等价、缺失 iOS 行为、迁移矩阵遗留由 V2 负责。逐任务选择一个主框架。
- `harmony-maintenance`：HarmonyOS 日常规则是唯一默认主框架；V2 资料只作为历史证据，不继续执行迁移门槛。

切换生命周期必须更新 `.ai-project/lifecycle.json`。保留 `.migration-v2`，不要删除迁移历史。切换到日常维护不等于宣称迁移矩阵全部通过；它只表示后续工作按产品维护和发布风险管理。

`migration-transition` 中意图不清时使用 `.codex-rules/_shared/framework_router.py` 做确定性选择。普通 HarmonyOS 工作不因项目存在迁移历史而触发 V2；只有请求明确涉及迁移等价、缺失 iOS 行为或迁移证据才回到 V2。

“iOS 一致性”和“与 iOS/苹果端流程不一致”视为明确的等价遗留。批量反馈先用 V2 做 T3 联合诊断，再拆分实现切片；不因此把项目生命周期切回 `migration-active`。
