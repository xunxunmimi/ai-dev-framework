# HarmonyOS 日常开发规则

适用于普通 HarmonyOS、ArkTS 和 ArkUI 项目。它按 D0-D4 控制上下文和验证范围，小 Bug 不自动触发全工程扫描，大功能和高风险能力再逐步扩大检查。

它不是 iOS 迁移框架。iOS 到鸿蒙迁移应使用 `ios-to-harmony-migrator-v2`。

## 安装到项目

```bash
./setup-project.sh "/你的/HarmonyOS项目绝对路径" harmony-maintenance
```

脚本会创建项目内 `.codex-rules/harmonyos/`，并向根 `AGENTS.md` 加入一条短入口，不覆盖已有项目规则。另一台电脑复制整个规则文件夹后执行相同命令即可。

同时生成项目能力候选缓存与框架安装清单。能力缓存不授权执行未确认的 product、module、buildMode 或签名命令；框架升级前用套件根目录的 `installers/audit-project.sh` 检查本地修改。

由迁移项目进入提测、修 Bug 和上线阶段时使用：

```bash
./setup-project.sh "/你的/HarmonyOS项目绝对路径" migration-transition
```

此模式不会删除 `.migration-v2`。普通维护由鸿蒙日常规则负责，只有迁移等价遗留按任务调用 V2。

安装后编辑项目内 `.codex-rules/harmonyos/PROJECT.md`，填写实际模块、API Level、构建和测试命令。

## 使用

```text
修复这个 ArkUI 页面返回后状态丢失的问题，按项目日常开发规则进行最小充分验证。
```

```text
开发新的文件导出功能。先判断任务等级，确认权限、错误和恢复路径后分阶段实施。
```

只有需要 ArkTS 合规检查或编译器诊断时才使用 `arkts-code-check` 或 `ets-lsp-check`。

可选的蓝湖 MCP 和 `ponytail-review` 也由 D0-D4 自动路由：蓝湖只在存在设计源且任务需要视觉证据时按范围读取；`ponytail-review` 只在 D2/D3 最终 Diff 出现复杂度信号时最多运行一次。规则包不会自行安装工具，工具不可用时继续按项目现有证据工作。

任务等级由 Codex 自动判断，不需要用户每次声明。D0/D1 默认不启动模拟器、不写新测试、不截图、不生成独立报告，应用运行验收交给用户；发现路由、数据、权限、共享状态或跨模块影响时自动升级。

代码质量同样按等级加载：D0/D1 只在最终 Diff 内检查命名、职责和必要注释；D2 只检查受影响文件；D3 才增加模块边界、公共接口和扩展性审查。注释解释原因、约束、状态所有权和生命周期，不追求逐行注释，也不会因此自动运行模拟器或生成报告。

明确的小问题不运行分类脚本；范围不清、D2+ 或脏工作区才用脚本保存任务基线。分类只看本任务 Diff，不会因为历史迁移资料或旧未提交改动扩大验证。

## 自检

```bash
python3 scripts/self_test.py
```
