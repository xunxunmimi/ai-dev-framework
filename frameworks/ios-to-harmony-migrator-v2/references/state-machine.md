# 确定性迁移状态机

`.migration-workspace-v2/state/migration-state.json` 是迁移执行阶段的唯一事实源；`.migration-v2/project.json.phase` 只是兼容镜像。不得由 Agent 直接手改状态，应调用 `scripts/migration_state.py transition`。

状态事件：

| 事件 | 前置条件 | 结果 |
|---|---|---|
| `discovery_ready` | Phase 0 门禁通过 | `planning/active` |
| `plan_ready` | 计划存在且绑定当前发现哈希 | `planning/waiting-human` |
| `plan_approved` | 当前计划的人工批准仍有效 | `implementation/active` |
| `slice_started` | 计划包含该切片，且没有活动切片 | 设置 `activeSlice` |
| `slice_verified` | 当前切片 Gate 报告通过 | 记录完成并清空活动切片 |
| `slice_blocked` | 当前切片存在且给出原因 | `implementation/blocked` |
| `slice_resumed` | 已阻塞切片获得继续条件 | `implementation/active` |
| `migration_transition` | 计划切片全部完成 | `transition/active`，日常鸿蒙规则接管普通任务 |
| `release_candidate` | T4 报告通过 | `release/active` |
| `released` | 发布证据通过 | `release/complete`，切换为鸿蒙日常维护 |

每次成功转换追加到 `state/events.jsonl`。失败不改状态。网络、模型或构建失败不得直接跳过节点；失败写入 `slice_blocked`，继续必须显式记录恢复原因；本工具不自动循环修复。

2.5：`slice_started` 传 `--task-id`，重新核验批准与项目身份。切片/发布报告必须来自本任务、当前代码、同配置和计划，且覆盖完整 Profile；T4 不能使用 T2 通过报告。状态 JSON 原子写入、转换串行锁定；JSON 是事实源，事件/Markdown 是派生视图，异常中断后先检查事实源再重建视图，不能用旧 Markdown 覆盖状态。

2.6 新增 slice_reopened：提供新 task-id、已完成 slice 和矛盾反馈 reason，先 --dry-run 可无写审查。保留旧报告引用并标记失效，重新验证相关切片，不自动重做全迁移。release_candidate/released 必须有 verificationPurpose=release 和 releaseContractPassed，targeted/Debug 不可替代。
