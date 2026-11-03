---
name: unslop-ui
description: Improve a selected project's new UI or explicitly requested UI polish using unslop-inspired visual review. Requires project-local opt-in; preserve supplied designs and do not use for ordinary bugs, parity migration, or unselected projects.
license: CC-BY-4.0
metadata:
  author: "Framework adaptation of unslop by Damir Sokolovsky (@heydamir)"
---

# 按项目选择的 UI 设计与优化

本能力改编自 Damir Sokolovsky（[@heydamir](https://x.com/heydamir)）的 [unslop](https://github.com/heydamir/unslop)，遵循 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。保留 [NOTICE.md](NOTICE.md) 与 [LICENSE.md](LICENSE.md)。这不是上游完整安装包。

## 先确认项目选择与本次范围

项目必须通过独立接入工具安装到该项目的 `.agents/skills/unslop-ui/`，并有绑定该项目根目录的 `selection.json`。源码仓库中的能力目录、全局副本、另一个项目的选择记录都不构成启用。没有选择记录时立即退出，不读取上游设计参考、不修改 UI，也不代用户创建选择记录。

开始前检查用户本次是否要求新 UI 或 UI 优化、具体允许文件，以及现有设计稿、品牌规范、截图和设计 token。不要把“没有找到设计文件”视为“没有设计”。设计状态不明时先澄清；普通 Bug、编译、性能、迁移等价任务保持主框架流程。

运行只读范围检查，按本次实际事实填写参数：

```bash
python3 <本项目技能目录>/scripts/check_ui_scope.py \
  --project <项目根目录> --intent new-ui --design-status none \
  --file <本次目标文件>
```

只有输出 `eligible: true` 时才继续。它要求项目、意图和所有文件与项目选择一致；调用参数不能代替真实用户意图。主框架的读写权限、任务边界、验证预算仍然优先。此工具给出决策，不是文件系统沙箱。

## 已有设计优先

- 选择记录或当前事实任一表明已有设计，就不能以 `new-ui` 开始另起风格。
- 明确请求 `optimize-ui` 且已有设计时，只审查设计还原度、可访问性及用户指定的问题，修正确认的偏差。保留设计稿规定的配色、渐变、字体、图标、布局和品牌元素；更大的改版只提出建议，等用户明确改变设计约束后另行处理。
- 本次为 `new-ui` 且确认无设计时，从产品目标、用户和内容提出方向。已有 UI 优化先找具体问题，再提出与现有产品一致的修正。

## 视觉工作

读取实际涉及的屏幕和相邻组件。先改善内容层级、分组、对齐、操作清晰度和可访问性，再考虑装饰。保留功能、数据、文案事实、导航和状态所有权。

需要系统视觉审查时，按需阅读 [上游问题清单](references/upstream-signs.md)；需要起始间距与层级参考时再读 [上游 token 示例](references/upstream-tokens.md)。这些是网页场景中的启发式材料，不能覆盖已确认设计，也不能把每个相似元素都判为缺陷。不要机械地统一成同一套配色或删除品牌渐变。

用产品、视觉性格、内容世界、主要视觉焦点和一个识别细节简短说明方向；方向已经清楚时继续授权工作，只有关键选择尚不明确时才请用户决定。小调整直接按现有系统完成，不强制完整重设计流程。

网页沿用项目已有组件和 token。SwiftUI/UIKit/ArkUI 使用平台的语义控件、排版、触控尺寸、系统图标和动态字体规范，不照搬网页 px、Lucide 或字体建议。SwiftUI 深度视觉工作可按项目选择既有 `swiftui-visual-design-review`，仍只保留一个明确的任务方向。

本适配不包含上游的网页抓取、故事视频、图片生成和依赖安装。不要自动添加 Playwright、Chromium、ffmpeg、外部字体、远程素材或付费服务。

## 验证与交接

按主框架预算核对受影响状态、可访问性和实际渲染；没有运行条件时明确说明未验证部分。报告具体修改、保留的设计约束和实际执行的检查。代码检查通过不代表视觉验收或设备验收完成。
