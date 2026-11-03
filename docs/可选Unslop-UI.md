# 按项目启用 Unslop UI

`unslop-ui` 是可选的次级视觉能力。适合确认没有 UI 设计、需要建立方向的项目，或用户明确要求优化 UI 的具体页面。能力源码进入框架不等于任何项目已启用。默认安装流程、全局技能注册、现有项目和版本绑定都不改变。

本适配基于 [Damir Sokolovsky 的 unslop](https://github.com/heydamir/unslop)，固定在 `a6f121eee5f29c2f74976b75456feae8b66afb3a`，保留 [归属与修改说明](../capabilities/unslop-ui/NOTICE.md)、完整 CC BY 4.0 许可和上游引用指引。当前上游支持项目内 `.agents/skills/`（Codex）或 `.claude/skills/`（Claude Code）；此框架的接入工具只支持 Codex 项目内安装，没有全局模式，也不访问 `.claude` 配置。

## 选择一个项目与明确文件

项目须已有经用户确认的 `.ai-project/read-policy.json`，并允许本次项目内技能路径。没有策略或策略不允许时工具拒绝接入，不自动创建或扩大读取授权。未接入主框架的项目，也应先按项目实际授权准备读取策略；不要复制其他项目的根路径或授权。

在框架源码目录先预览：

```bash
python3 installers/enable_unslop_ui.py \
  --project /path/to/project \
  --intent new-ui --design-status none \
  --file Sources/NewScreen.swift
```

确认该项目确实没有设计且这些文件是允许范围后，同一命令加 `--apply`。多个文件重复传 `--file`，只能是精确相对路径，不接受整个项目通配。工具仅新增该项目的 `.agents/skills/unslop-ui/` 和自身安装锁；技能、许可及选择记录在同一个目录内发布。不改 `AGENTS.md`、主框架安装清单、生命周期、任务基线或共享入口。已有同名目录一律拒绝覆盖。

已有 UI 需要优化时选择 `--intent optimize-ui`；存在设计稿或已确认的品牌/截图规范时必须填 `--design-status existing`。`new-ui + existing` 会被拒绝。不得仅因为设计文件不在当前目录就填 `none`。

## 每次 UI 任务仍要检查

在选中的项目中可显式使用 `$unslop-ui`；正常技能发现也必须服从项目选择和当前任务范围。先运行项目内技能的检查器：

```bash
python3 .agents/skills/unslop-ui/scripts/check_ui_scope.py \
  --project /path/to/project \
  --intent optimize-ui --design-status existing \
  --file Sources/ExistingScreen.swift
```

检查通过退出码为 `0`，关闭或受阻为 `2`。缺少当前 UI 意图、设计状态未知、文件超出选择、复制了另一个项目的选择记录、禁用或没有选择记录时均关闭。设计状态取选择记录与当前事实中更保守的一方，不能用 `none` 覆盖已记录的设计。

| 情况 | 结果 |
|---|---|
| 普通项目安装，未选择本能力 | 不安装、不启用 |
| 选择新 UI，确认无设计，当前请求及文件匹配 | 提出产品方向，再按已授权范围实现 |
| 选择优化 UI，有设计 | 仅按设计稿修正已确认偏差；不允许另起风格 |
| 有设计却要求作为无设计的新 UI 接管 | 拒绝，已有设计优先 |
| 普通 Bug、迁移等价、未知设计或未选择页面 | 不介入 |

停止使用时，将本项目技能目录 `selection.json` 中的 `enabled` 改为 `false`；检查器立即关闭，不需要卸载全局技能。之后如需改变意图或文件范围，先人工核对并编辑该项目的选择记录，再重新检查；这不授权修改业务文件。选择记录只表达项目偏好，不替代每次用户请求和主框架的读写边界。

## 能力范围

引入视觉问题审查、层级、间距、内容焦点和产品方向的思路。上游网页 token 是参考值，不能覆盖原设计，也不能直接作为 SwiftUI/UIKit/ArkUI 的控件尺寸或字体规范。已有品牌中的渐变、色彩或装饰不会因为上游偏好被自动删除。

不引入网页抓取、故事视频、图片生成或自动依赖安装；无需 Playwright、Chromium、ffmpeg 或付费服务。此改动是源码候选集成，未发布新正式版本、未安装到真实项目。`IMPORT-PROVENANCE.json` 继续描述历史导入基线；包含新增能力的当前源码应以提交及内容摘要识别，不能称为原始 2.6.5 固定发布。

`check_ui_scope.py` 是供智能体遵守的决策检查器，不拦截任意编辑器的写入。自动化测试覆盖选择、范围、设计优先和项目间隔离；它们不证明真实产品的视觉质量，也不替代渲染和用户验收。

维护者可运行 `PYTHONDONTWRITEBYTECODE=1 python3 -B installers/optional_ui_self_test.py`；测试只在临时合成目录安装和写入，不需要模型调用、网络或真实项目。
