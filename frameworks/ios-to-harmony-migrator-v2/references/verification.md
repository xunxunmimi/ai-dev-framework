# 最小充分验证

## 原则

- 先运行最靠近改动的检查，再运行共享依赖的必要回归。
- 相同源码、依赖、工具链、设备环境和产物哈希下，允许复用仍有效的通过证据。
- 首次通过后，只有代码/依赖/环境变化、新失败或未解决风险才扩大或重复测试。
- T0/T1 默认不生成门禁报告、日志、截图、矩阵和哈希；运行验收交给用户。
- T2 不为未改动页面重新生成全量截图、矩阵和哈希。
- T4 必须重新绑定最终提交和最终安装包，不能复用旧产物的发布结论。

## 推荐门禁组

| Profile | 门禁 |
|---|---|
| T0 | 无默认门禁；Diff/格式按需检查 |
| T1 | 无默认门禁；Diff + 必要时 changed-static/targeted-build |
| T2 | affected-unit、targeted-build、affected-contract；standard-device/affected-ui 按行为风险 |
| T3 | T2 + module-build、shared-regression、traceability |
| T4 | all-build、all-tests、all-ui、platform-device、security、release |

T0/T1 不调用 `run_gates.py`，除非用户明确要求或代码事实导致升级。T2-T4 在 `.migration-v2/gate-config.json` 中把真实命令映射到 Profile。使用：

```bash
python3 scripts/run_gates.py .migration-v2/gate-config.json --task-id 当前任务ID --slice-id 当前切片ID --profile T2 --report .migration-workspace-v2/reports/gates-T2.json
```

可用 `--gate <id>` 进一步限定。只有门禁显式设置 `cacheable: true`、输入内容及 `fingerprintValues` 中的工具链/设备标识一致时，`--reuse-valid` 才复用通过结果；T4 永不复用旧发布结论。空 Profile 或未配置的必选门返回 blocked，不静默跳过。

2.5：先按共享 task-control 初始化任务；Gate 报告绑定 task-id、slice-id、profile、实际代码摘要、配置/计划、命令和时间。手工证据也须绑定当前任务并标注 reviewer/finishedAt，文件内写 passed 不能代替真实验收。执行前后代码或配置变化会失败。缓存只有显式环境 fingerprintValues 和当前绑定一致才可复用；T4 不复用。状态机只接受完整 Profile，`--gate` 子集报告不能用于关闭整个切片。
