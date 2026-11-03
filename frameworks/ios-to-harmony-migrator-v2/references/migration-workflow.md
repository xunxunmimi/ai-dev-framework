# 完整迁移工作流

## 新迁移

1. 运行 `scripts/analyze_source.py`，只读生成完整源索引和 Phase 0 发现基线。
2. 完成工程结构、架构模块、页面导航、UI/交互状态、功能链路、三方库、数据、网络、权限系统能力、资源本地化无障碍以及测试构建发布分析。扫描命中只是候选，必须回到源码或运行证据确认。
3. 运行 `scripts/check_discovery_gate.py`。门禁未通过时只能继续源分析、补证据或澄清，不得开始 HarmonyOS 业务实现。
4. 生成符合 `schemas/migration-plan.schema.json` 的计划，并绑定发现基线与源索引 SHA-256。首次业务实现前由用户确认并运行 `scripts/plan_approval.py approve`；该命令会再次验证 Phase 0。
5. 按可独立构建和回滚的竖切迁移。先冻结状态与副作用，再实现 ArkTS/ArkUI。
6. 每个切片运行其等级对应的门禁；只更新受影响的覆盖和追踪项。
7. 在计划里程碑做整合回归；发布前执行 T4。

计划批准同时绑定计划、发现基线和源索引 SHA-256。任一内容变化后 `check` 必须失败；先补受影响分析，再重新提交用户确认。AI 不自行把未确认计划标记为批准。

## 源端证据优先级

1. 源码、Storyboard/XIB、资源和现有测试。
2. 已有、版本匹配的截图/录屏/日志。
3. 仍有歧义时运行 iOS 模拟器采集。

不要为了报告格式重复启动模拟器。证据必须绑定源码版本、Fixture、设备、语言、主题和状态。

## 状态

使用 `unanalysed`、`extracted`、`in_progress`、`implemented`、`pending_verification`、`verified`、`blocked`、`deprecated`。完成百分比只来自覆盖项，不从文件数量或测试数量推断。
