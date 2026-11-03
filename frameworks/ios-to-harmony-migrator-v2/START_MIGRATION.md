# 新项目入口

把源项目和目标目录告诉 Codex：

```text
使用 $ios-to-harmony-migrator-v2。
iOS 源目录：/absolute/path/ios-app（只读）
HarmonyOS 目标目录：/absolute/path/harmony-app
先完成 Phase 0 源项目发现门禁，覆盖架构、UI/交互、功能、三方库、数据、网络、权限、资源和测试发布，再给出绑定哈希的迁移计划；确认后才能实施。
```

后续任务由分类器自动判断 T0-T4，用户无需声明大小。T0/T1 默认不启动模拟器、不截图、不新增测试、不生成独立报告，应用运行验收交给用户；发现页面/功能迁移、导航、数据、权限、共享状态或跨模块影响时自动升级。

初始化命令：

```bash
./installers/setup-project.sh migration \
  /absolute/path/ios-app /absolute/path/harmony-app
```

初始化只生成源索引和待填写的发现基线，不代表可以开始写鸿蒙业务代码。完成基线后运行：

```bash
python3 frameworks/ios-to-harmony-migrator-v2/scripts/check_discovery_gate.py \
  /absolute/path/harmony-app/.migration-workspace-v2 \
  --report /absolute/path/harmony-app/.migration-workspace-v2/reports/discovery-gate.json
```

门禁通过后生成迁移计划并请求用户批准。之后处理已批准切片、Bug 或功能时直接描述需求。项目内的 `.migration-v2/AGENTS.md` 会按需分类并只读取 `task-context.json.mustRead`。进入提测和日常修复阶段后运行 `./installers/setup-project.sh harmony-transition /absolute/path/harmony-app`，普通鸿蒙任务由日常框架接管，迁移等价遗留继续按任务使用 V2。
