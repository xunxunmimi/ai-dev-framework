# 模型与推理策略

模型选择是项目策略，不写死在业务规则中。Skill 不能保证自动切换当前 Codex 任务的模型；运行界面或调用方不支持切换时，记录建议并继续使用当前模型。

| 工作 | 能力等级 | 默认推理 |
|---|---|---|
| 清点、分类、结构化报告 | cost_efficient | low |
| T1 Bug 和定向修复 | balanced | low/medium |
| T2/T3 普通迁移实现 | balanced/strong | medium |
| 复杂状态机、平台替代、顽固问题 | strong | high |
| UI 里程碑、架构和发布终审 | highest | high |

项目 `project.json` 的默认映射为空。只从当前运行环境、账户明确支持的模型中配置 cost_efficient/balanced/strong/highest；未取得支持清单时继续当前模型，记录建议未应用。不请求付费 API 来探测模型。

不要默认使用 xhigh/max/ultra。两次无进展、跨模块歧义、高风险决策或最终审查才升级模型或推理等级。机械扫描、Schema、Diff、日志解析和评分优先交给脚本，而不是更强模型。
