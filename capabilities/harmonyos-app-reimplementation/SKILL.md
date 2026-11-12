---
name: harmonyos-app-reimplementation
description: Independently reimplement an owned HarmonyOS app with identical business behavior and visible copy but a new logo/reference-led UI. Use only for an explicitly selected reimplementation (马甲包) project, not ordinary bug fixes, branding-only changes, or iOS parity migration.
---

# 鸿蒙应用独立重实现

目标：业务行为、功能和用户可见文案不变；自有 UI、布局、图标与实现重新设计。不是复制工程后批量改名，不是审核规避工具，不承诺重复应用审查或商店上架结果。

## 启用与范围

本能力是可选能力，不是第四个主框架。目标项目先接入 HarmonyOS 日常主框架；仅当用户明确要求本次独立重实现，且本项目 `.agents/skills/harmonyos-app-reimplementation/selection.json` 的 `enabled` 为 true、`projectRoot` 与当前规范路径一致时使用。从 selection 读取 sourceRoot 后，重新验证源/目标读取策略；选择记录不授予业务读取、编辑、上传或收费服务权限。仅有源码目录或复制来的其他项目选择记录不算启用。用户暂停/撤销后停止，普通 Bug 不扩展为全量重写。

首次安装见框架 `docs/鸿蒙独立重实现Skill.md`。源码/目标须分开且互不包含，源只读。已有目标修改先保留并核对；不覆盖并发工作，不复制源 `.git`、凭据或整个控制目录。先记录源 commit/dirty 状态与目标基线，不擅自提交用户修改。

输入：源项目、目标项目、授权范围、Logo、几张 UI 参考图、平台/API/设备约束、允许保留的依赖与新项目配置。配置缺失可以留显式占位，禁止借用原应用生产身份冒充联调通过。缺少图片先做行为盘点，不能声称已完成视觉设计。只有实际阻塞的一项需要问用户，其他独立工作继续。

## 执行顺序与交付

1. **先提取合同再编码。** 阅读 [行为与重实现](references/behavior-contract.md)，建立可追溯功能/页面/状态、文案、多语言、数据与外部合同清单。以源码、产品需求和实际运行交叉核对；未知项标记 unknown，不猜实现。
2. **从参考图生成新视觉系统。** 阅读 [视觉设计](references/visual-direction.md)，查看真实图片像素，提取元素而不是复制整张参考。形成 token、组件与代表页，再覆盖全部自有页面、弹窗和状态。文案不改；系统/SDK UI 单独列例外。
3. **独立设计并分片重实现。** 基于行为合同重新设计模块边界、状态流和内部接口；以有意义的英文标识命名代码与资源、以准确的新注释解释新实现。不能把随机改名、调换函数顺序、复制旧注释或语法替换当成重写。所有自有模块需有新实现映射；临时调用旧自有实现明确记为未完成。按主框架继续有效任务，不为每片重复创建新任务。
4. **逐片验证并恢复推进。** 阅读 [验证与交付](references/verification.md)。行为等价与视觉新设计是两条独立验收线；验证实际文件/返回值/状态，不只看 Toast。完成一片记录结果并继续下一未完成项；受阻只暂停受影响项。恢复时读当前合同、代码变更和未完成清单，不把历史“已完成”当最新事实。
5. **报告事实。** 已实现、已静态检查、已构建、已真机验证、未测、配置阻塞分别列出；没有真实证据不宣称全量完成。上传、合并、发布按当前明确授权执行；本能力的安装不自动授权这些操作。

不自动安装图片生成、浏览器或商业服务依赖。可用且获准的绘图/生成工具用于新资产；不可用则先实现授权范围内的原生/矢量资源并明确差距，不把占位图当最终切图。参考和产物保留授权来源。
