> Current source version: `2.6.11-harmony-reimplementation`. Optional Skill and upgrade boundaries: [鸿蒙独立重实现 Skill](docs/鸿蒙独立重实现Skill.md). Previous version notices below are historical.

> Current source version: `2.6.10-migration-bootstrap`. See [migration authorization bootstrap](docs/迁移初始化与读取授权.md). Previous version notices below are historical.

> Current source version: `2.6.8-harmony-workspace-ignore`. See [workspace policy and upgrade compatibility](docs/鸿蒙迁移工作区Git规则.md). The version notices below are historical.

> 本分支为 `2.6.7-task-handoff-review` 审阅候选，尚非正式 release。本轮支持精确 UI 状态确认、代码交付释放与显式新任务接续，兼容/回退和离线应用限制见 [交付说明](docs/2.6.7-代码交付与精确元数据确认.md)。上传分支不会自动更新任何项目，实际安装须核确切提交和项目回执。下面保留原套件说明。

# AI 开发框架套件 2.6.1

这是三个互斥主框架的唯一源码目录：

- `frameworks/ios-to-harmony-migrator-v2`：iOS → HarmonyOS 迁移与迁移等价遗留。
- `frameworks/ios-daily-development-rules`：Objective-C、Swift、UIKit、SwiftUI 日常维护。
- `frameworks/harmonyos-daily-development-rules`：HarmonyOS、ArkTS、ArkUI 日常维护与上线。

`shared/` 不是第四个框架。它只提供三者共用的任务分级、上下文预算、生命周期、验证预算和代码质量契约，减少重复规则和误触发。

`capabilities/` 保存可选的窄能力，不是主框架，也不会默认接入所有项目。当前包含：

- `swiftui-visual-design-review`：SwiftUI 视觉方向、品牌一致性、动效和渲染验收；只在相关 iOS 项目的 `.agents/skills/` 中按需接入。
- `unslop-ui`：按项目与具体文件选择的新 UI/明确 UI 优化能力，默认关闭，已有设计优先。独立接入方式见 [可选 Unslop UI](docs/可选Unslop-UI.md)；原安装入口不会安装它。

- `harmonyos-app-reimplementation`：显式选择的鸿蒙马甲包/独立重实现。功能、逻辑和文案保持一致，依据 Logo 与参考图重新设计全部自有 UI，并重新实现自有代码。安装、触发示例与验收边界见 [鸿蒙独立重实现 Skill](docs/鸿蒙独立重实现Skill.md)。普通项目默认不启用。

## 生命周期

| 项目状态 | 默认主框架 | 何时调用迁移 V2 |
|---|---|---|
| `ios-maintenance` | iOS 日常 | 不调用 |
| `migration-active` | 迁移 V2 | 始终 |
| `migration-transition` | HarmonyOS 日常按任务接管 | 仅迁移等价、iOS 行为缺失、迁移矩阵遗留 |
| `harmony-maintenance` | HarmonyOS 日常 | 默认不调用；只读历史证据时按需 |

同一任务只能有一个主框架。语言检查、模拟器、蓝湖和 Ponytail 都是窄能力，不得扩大任务范围。

## 安装与使用

```bash
./installers/install-this-mac.sh
./installers/doctor.sh
```

项目接入：

```bash
# 普通 iOS 项目
./installers/setup-project.sh ios-daily /绝对路径/项目 --read-policy /已确认策略.json

# 普通 HarmonyOS 项目
./installers/setup-project.sh harmony-daily /绝对路径/项目

# 迁移项目
# 两个目录已有有效完整读取策略时：
./installers/setup-project.sh migration /绝对路径/iOS源 /绝对路径/Harmony目标

# 首次迁移：仅在用户已明确授权两个项目的完整迁移读取与策略登记时
# 两个目录须预先存在；授权说明使用实际用户原意，不填凭据，不把路径当作授权。
./installers/setup-project.sh migration "/绝对路径/iOS源" "/绝对路径/Harmony目标" \
  --init-missing-read-policies \
  --authorization-source "用户明确要求将此 iOS 项目迁移至指定鸿蒙项目，授权读取两项目业务代码并登记范围"

# 已进入提测/修 Bug 的迁移项目
./installers/setup-project.sh harmony-transition /绝对路径/Harmony项目
```

日常任务直接描述需求即可。明确的小问题走快速通道；只有范围不清、D2/T2 以上或工作区已有大量修改时才运行共享分类器生成任务上下文。

代码质量按同一等级预算执行：小问题只做最终 Diff 自检；普通功能加载受影响平台规则；跨模块任务才检查公共接口和扩展性。不会因为要求可读性或注释就自动启动模拟器、安装工具或生成独立报告。

新迁移增加 Phase 0 硬门禁：初始化先生成源索引，完整分析架构、页面交互、功能、依赖、数据和平台能力后才能批准迁移计划。源索引、发现基线或计划任一变化都会使旧批准失效；全量文件清单保留在索引中，默认上下文只读取紧凑基线。

2.4 增加项目安装清单与安全升级审计、迁移确定性状态机、需人工确认的 Gate 探测和 UI 状态证据清单。2.4.1 增加批量 iOS 一致性反馈的确定性路由、否定防误触发、T3 联合诊断后拆切片，以及 14 个零模型生命周期路由正/负对照。LangGraph 可以在这些确定性边界之上做编排，不应复制框架规则。

已接入项目升级前先审计：

```bash
./installers/adopt-project.sh /绝对路径/旧项目   # 仅旧项目首次建立升级基线
./installers/audit-project.sh /绝对路径/项目
./installers/update-project.sh /绝对路径/项目 --dry-run
./installers/update-project.sh /绝对路径/项目
```

受管文件有本地修改时更新会拒绝覆盖；先人工合并，不提供强制覆盖开关。

## 目录原则

- `frameworks/`：唯一可编辑源码。
- `capabilities/`：可选窄能力源码；由项目显式接入，不参与主框架互斥路由。
- `shared/`：公共规则与确定性脚本。
- `installers/`：本机安装、项目接入、诊断和发布打包。
- `evals/`：三框架的隔离基准、确定性评分与版本对比；普通开发不会自动运行。
- `releases/`：给另一台电脑使用的完整发布包。
- `archive/`：V1 与双模型历史框架，只读保留。
- Cookie、证书、Token 与签名文件永不进入本目录或发布包。

完整操作入口见 [docs/三框架使用指南.md](docs/三框架使用指南.md)。`项目创建工具/` 与本目录平级，保存 `ai-dev-kit` 和 `vibe-playbook-v2`，不与三个维护/迁移框架混用。

## Eval：用数据决定是否继续优化

Eval 不是第四个框架，也不会在日常任务中增加上下文。`doctor.sh` 只运行零模型调用的自检；只有显式执行 `run` 或带 `--execute-all` 的 `run-suite` 才会调用模型并消耗 Token。

```bash
# 免费自检与查看用例
python3 evals/scripts/self_test.py
python3 evals/scripts/run_eval.py list --suite quick

# 发布候选先跑单例；确认后再决定是否跑完整 12 例
python3 evals/scripts/run_eval.py run \
  --case ios-quick-d1-nil-guard \
  --framework-version 2.5.1 \
  --model gpt-5.6-terra --reasoning low
```

24 个真实用例是目录和验收契约，必须在固定 revision 的独立 worktree 中运行。完整说明见 [evals/README.md](evals/README.md)。

## 2.5 任务控制与中文注释

新增共享任务控制模块（身份/隔离基线/原子状态/恢复/绑定验证），按需使用，见 [任务控制约定](shared/context-engineering/task-control.md)。小任务保持轻量。新增或改动的自有函数、成员/属性/常量和重要计算需准确中文说明；人工核对语义，排除生成代码与第三方。工具只发现差异，不是防写沙箱；真实项目验证由用户选择项目后执行，本套件自检不证明业务正确。

## 2.5.1 读取边界

接入前先按真实授权准备 `.ai-project/read-policy.json` 或通过 `--read-policy` 传入。没有策略不扫描项目。部分白名单检查明确标记 partial；不执行无法约束读取范围的外部编译/测试，不把跳过内容算通过。详见 [读取边界](shared/context-engineering/read-boundary.md)。已有项目先审计升级，不批量覆盖规则。

## 2.5.2 范围来源修正

长期项目策略与临时任务范围分离。控制接入任务的 partial 白名单放入对应任务目录，不再变成其他对话的共享业务禁读；项目真实禁止项仍继承，平台拒绝按具体操作暂停记录，不猜测目录黑名单。旧版策略兼容但不自动放宽，纠错须保留来源审计。详见 shared/context-engineering/read-boundary.md。

## 2.6.0 分级与证据修正

任务难度、范围和风险独立记录；简单任务保留快速通道，高风险定向检查与真正发布分开。迁移源索引统一读取边界；旧 UI 路径证据保持可读但不自动通过；已完成切片可受控重开。新增零模型夹具演练、固定反例集、实际版本/比较合同和事务恢复工具。详见 [2.6.0 变更与操作](docs/2.6.0-分级优化与回滚.md)。全局技能、已有业务项目和另一台电脑不会自动升级。

## 2.6.1 任务确认与临时外部只读

精确确认 Xcode UI 元数据而保留原基线；任务绑定一次性外部只读许可不会扩大全局策略。见 [2.6.1 更新与跨机复制](docs/2.6.1-任务元数据与外部只读.md)。

凭据路径排除、覆盖限制与升级说明：[2.6.2 凭据排除](docs/2.6.2-凭据排除.md)。

本机敏感配置排除及升级说明：[2.6.3 本机配置排除](docs/2.6.3-本机配置排除.md)。

部分范围任务受控回写：[2.6.4 使用说明](docs/2.6.4-部分范围受控回写.md)。

任务专用安全引导：[2.6.5 使用说明](docs/2.6.5-任务专用安全引导.md)。
