# 鸿蒙迁移工作区 Git 规则

适用：所有鸿蒙项目，包括 iOS 迁移、过渡期维护和普通鸿蒙开发。

规则入口：鸿蒙日常工作流 `templates/WORKFLOW.md` 和迁移 V2 `references/core-contract.md`。忽略片段：`frameworks/harmonyos-daily-development-rules/templates/migration-workspace.gitignore`。仓库自身也忽略两种迁移工作区。

升级规则不等于所有现有项目已经修改。现有项目须按原有 audit/update 流程接入，不绕过活动任务保护、不直接覆盖规则；项目 `.gitignore` 是用户文件，仅在本次授权范围内保留原内容并追加片段，不覆盖整个文件。

`.migration-workspace/`（旧版）和 `.migration-workspace-v2/`（V2）都不上传，但保留本地。目录可能仍是迁移状态、批准和测试证据的依赖，不能概括为“全是历史缓存”。App 代码、打包资源应在正常源码目录，迁移脚本的依赖则应单独保留/交接。

## 提交前验证

在项目 Git 根目录运行：

```sh
git check-ignore --no-index .migration-workspace/probe.json .migration-workspace-v2/state/probe.json
# 不仅检查未跟踪项；还要检查 Git 索引和暂存变更。
git ls-files | grep -E '(^|/)\.migration-workspace(-v2)?/'
git diff --cached --name-only | grep -E '(^|/)\.migration-workspace(-v2)?/'
```

第二、三条输出需要审查；无匹配时 grep 返回 1 是正常现象。忽略规则不移除已跟踪数据。需要停止跟踪时先确认本地保留、验证工具依赖、团队交接及用户授权，再单独处理；不在应用代码提交里隐式批量删除，也不清除历史。
