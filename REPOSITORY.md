# 2.6.10 explicit migration bootstrap

Version `2.6.10-migration-bootstrap`, based on main `5f28215406407c9dcea268f0ccdc343cb0c56599`. PR #4 (2.6.9 source recovery) is independent and is not included. Task protocol, state schemas, credential exclusions and existing update gates are unchanged. No installed project or pinned release is automatically upgraded.

# 2.6.8 Harmony workspace policy

Version `2.6.8-harmony-workspace-ignore`, based on main `0450d4dcdeaad2bba35cadb1b0f97728e5497e5b`. Task protocol remains 2; state schemas and upgrade gates are unchanged. Historical hashes below do not identify this revision.

# 本地隔离候选说明

本轮 `2.6.7-task-handoff-review` 基于 main `5b6e9d5e352fbbc80e06f3dd6555b8394ed1cd81`，未合并/未部署。任务协议仍为2；只有采用新元数据确认的任务使用明确2.1状态格式，旧运行器和旧安装器会拒绝该格式。代码交付单独使用既有closed终态，验收维度保留，后续编辑须新任务。详见 `docs/2.6.7-代码交付与精确元数据确认.md`；以下2.6.6说明为历史来源，不代表当前候选安装状态。

当前分支为 `2.6.6-lifecycle-review` 候选系列，基线 b79462ca4a3176b96d0bf125abd446cb16eeb32a，非正式 release。早期未部署描述属于隔离快照，当前状态以确切提交和各项目维护回执为准；稳定身份/显式承接/取消、generation 与安装协议详情见 `docs/2.6.6-隔离生命周期候选.md`。下列为基线历史说明，不证明任何项目已安装当前提交。

# 统一源码仓库

新增的 `capabilities/unslop-ui` 是独立可选层，默认不安装、不路由到现有项目。接入需项目选择及明确 UI 范围，已有设计优先，见 `docs/可选Unslop-UI.md`。其上游 CC BY 4.0 归属仅适用于该能力的相关材料，不改变其余源码的许可。`IMPORT-PROVENANCE.json` 的摘要保留为历史导入证据；加入可选层后的源码须按新的提交/内容摘要识别，不是原始固定 2.6.5，也不构成正式发布。

当前源码是基于 2.6.5 泛化文档和合成评测标识的 `2.6.5-import-review` 审阅候选，不是原始固定快照，也不是正式发布。活动源码及运行边界仍需按内容摘要识别；请阅读 `IMPORT-PROVENANCE.json`。

- 先前 2.6.1 基线保留在 Git 父提交；`provenance/ccmac-2.6.1.json` 和 `IMPORT-VALIDATION.md` 是通用化的历史来源与验证记录，不证明当前候选已发布。
- 原开发候选分支保持独立，未完成的任务跨版本承接不在当前源码中；旧任务继续使用原固定版本。
- 项目升级须逐项审计、确认活动任务、漂移和授权后执行；不要复制其他电脑的绝对路径或重建旧基线。
- 已知限制：历史版本的可信来源验证、活动任务升级门禁及 ACL/xattr 回写保障不完整。源码入库不等于这些问题已修复。
- 不纳入凭据、业务数据、任务状态、机器配置或临时产物；忽略规则仅为辅助，提交前仍须逐项审查白名单。
