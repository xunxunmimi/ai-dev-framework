# 任务控制与验证约定

先按 [读取边界](read-boundary.md) 核对 read-policy；无策略禁止扫描。部分模式仅做 D0 进程内配置检查，verify-config 报 partial（退出码 3），不能作为编译/测试/发布通过。 2.5

小任务先只读确认项目身份和风险，可以不落盘过程状态。需要交接、并行或 D2+ 时使用项目的 `.codex-rules/_shared/task_runtime.py`，未接入项目可使用套件原路径。

```bash
python3 .codex-rules/_shared/task_runtime.py identity --project /绝对工程 --platform ios
# 核对实际 Bundle ID/Scheme 后，显式选择并写入身份；鸿蒙选择 bundle-id/product/module/build-mode。
python3 .codex-rules/_shared/task_runtime.py identity --project /绝对工程 --platform ios --scheme 已确认Scheme --bundle-id 已确认ID --write
python3 .codex-rules/_shared/task_runtime.py init --project /绝对工程 --task-id 唯一ID --file 自有文件相对路径 --request 当前需求
python3 .codex-rules/_shared/task_router.py --project /绝对工程 --task-id 唯一ID --request 当前需求 --stage pre --file 自有文件相对路径
python3 .codex-rules/_shared/task_runtime.py checkpoint --project /绝对工程 --task-id 唯一ID --completed 已完成内容 --todo 待办 --blocker 阻塞原因 --next 一个下一步
python3 .codex-rules/_shared/task_runtime.py resume --project /绝对工程 --task-id 唯一ID
```

身份冲突、分支/revision 变化、恢复文件不符、同文件并行或禁改依赖变化时暂停；先只读解释差异，人工确认再调整任务，不能自动接受冲突。`state.json` 是事实源，`handoff.md` 是可重建视图；非等待文件锁与原子替换保护写入，进程退出自动释放锁。等待权限写 `--status waiting-permission`，保存阻塞与下一步，不能自行批准或释放范围给另一任务。

项目 `.ai-project/protected-paths.json` 的 `patterns` 可持久记录额外公共库禁改 glob，`--protect` 也可追加公共库路径 glob；Pods/Carthage/vendor/oh_modules 和第三方 bundle 默认只读，SPM 生成目录只读。开始时拒绝已有修改的编辑文件；若必须与用户修改同文件，先人工确认细粒度边界，本工具不自动合并。外部工具写源代码、通过 post_install/patch 改依赖同样违规。检查是前后差异发现机制，不是操作系统写入沙箱；被忽略的构建缓存不参与业务摘要，不承诺发现所有第三方位置，项目必须补充手工公共库路径。

验证命令先在 `.ai-project/verification-commands.json` 确认：

```json
{"commands":[{"confirmed":true,"kind":"compiled","profiles":["D2"],"argv":["项目已确认的编译命令"]}]}
```

`task_runtime.py verify --project /绝对工程 --task-id 唯一ID --profile D2 --kind compiled --argv 命令 参数` 执行该精确命令；自动测试用 automatedTests。报告包含任务、profile、项目身份、HEAD、当前文件摘要、原命令、开始/结束时间和退出码；源码变化、错任务或错 profile 拒绝旧 passed。输出只保存摘要，原日志按项目已有机制保留，不上传凭据。超时/权限拒绝保持未通过，任务中记录等待，不能把审批等待当命令成功。

完成维度分开：codeModified、compiled、automatedTests、realDevice、submitted、pushed；后两者指 Git 提交/推送。默认真机 waiting-user，提交/推送 not-run。编译通过不代表自动测试、真机、提交或推送已完成；真实 Git/设备动作不在本轮自动执行范围。人工验收回填须有实际证据，静态工具不能认证人工证据或注释语义。

最终 Diff 必须人工检查中文注释：每个自有函数/方法用途与适用契约，每个成员/属性/常量用途、单位和状态，重要局部变量/算法/兼容分支的原因和边界。只改本次涉及的自有代码；排除生成代码与第三方；注释不以行数验收。

## 临时范围不持久化为项目禁读

项目 read-policy 只保存长期用户约束；单次只读范围使用 init --task-read-policy，文件明确 scopeKind=task、taskId，并进入 .ai-project/tasks/<id>/read-policy.json。关闭不改项目授权，新对话不自动恢复最近任务；恢复需显式 ID。平台拒绝只按实际操作处理，不推导整个目录禁读，不改名重放。

## 2.6.1 精确元数据确认

确认前仅预览：`task_runtime.py ack-metadata --project /项目 --task-id ID --file 相对路径 --old-sha256 旧值 --new-sha256 新值 --reason 用户确认原因 --confirmed-by 确认人 --authorization-source 授权来源`；审核输出中的 previewSha256，再加 `--apply --preview-sha256 该摘要` 才保存。确认人和来源是记录，不是身份认证。当前仅接受 Xcode `*.xcodeproj/project.xcworkspace/xcuserdata/*.xcuserdatad/UserInterfaceState.xcuserstate` 的精确文件。旧值必须匹配不可变 baseline.json 和 state 基线，新值必须匹配当前安全读取结果。其他非本任务变化一律拒绝，原 baseline 不变；记录任务、路径、旧/新摘要和原因，备份原状态后原子提交，旧 passed 标记 stale。再次变化会重新阻塞。备份在任务目录 metadata-confirmations；中断后先读 state.json 与备份，不盲目重放。dry-run 不取得写锁，仅供预览，apply 会重新核验。

## 2.6.2-ruler-task-handoff 开发候选：承接受管升级

此候选未经独立审查，不用于业务项目。`accept-framework-upgrade` 以 `--project`、`--task-id`、人工审查过的起点 `--anchor-transaction-id` 和 `--anchor-sha256`、按时间顺序重复的 `--transaction-id`、待单独确认的 UI `--file`、`--old-sha256`、`--new-sha256`、`--confirmed-by` 和 `--authorization-source` 预览；审查后另传 `--apply --preview-sha256` 提交。UI 仍须另走 `ack-metadata`，升级承接不会放行它。旧基线和旧报告不重写；旧通过维度标记 stale。规则变化必须匹配完整事务链、原任务规则身份、最终安装清单及当前文件；其他范围外变化阻断。生命周期只能改变框架版本，起点来自事先核验过的历史事务。仅支持明列的 iOS 2.5.2、2.6.1 和本候选版本映射。原任务没有旧规则身份验证证据时不支持承接。事务摘要和授权来源均不是签名或身份认证；须由审查人核实历史来源，不能声称不可伪造。

另需重复传入三项 `--trusted-suite`，每项是包含 `version`、`suiteRoot`、`suiteSha256` 的 JSON 对象，值须来自审查人独立核验，而不能照抄待承接事务后自行宣称可信。旧 2.5.2 清单的原 suiteRoot 若已不存在，必须另行核对备份内容摘要；该字段保留历史原路径，不表示原路径可用于回滚。
