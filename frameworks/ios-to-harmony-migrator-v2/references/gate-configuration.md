# 项目 Gate 配置

初始化会探测 HarmonyOS 工程并生成候选 Gate，但候选默认 `confirmed: false`，不会自动执行未经确认的构建命令。先检查 `.migration-v2/gate-config.json`，再显式确认：

```bash
python3 scripts/detect_gates.py /目标项目 /目标项目/.migration-v2/gate-config.json --confirm harmony-build-debug
```

`T2/T3` 默认只选择受影响模块构建。`T4` 除构建外还必须包含人工/真机/签名/商店等项目适用证据；人工 Gate 读取一个状态为 `passed` 的 JSON 证据，不能用 Mock 或编译通过代替。

项目命令、模块、product、buildMode 或 SDK 变化后重新探测，但不得覆盖已确认的项目自定义 Gate。未知命令保持阻塞，不能猜测为通过。
