# 三框架 Eval

这套评测用于回答四个问题：框架是否提高完成质量、是否降低 Token、是否减少无效验证、是否减少错误分级和无关修改。它不是第四个开发框架，不参与普通项目自动路由。

## 两层数据集

- `cases/quick.json`：12 个可在隔离 Fixture 中执行的快速回归，三个框架各 4 个。
- `cases/real.json`：24 个来自当前使用场景的真实任务目录，三个框架各 8 个。真实项目必须先固定 Git revision，再在独立 worktree 中执行；不得直接修改日常工作目录。
- `cases/framework-routing.json`：14 个生命周期路由正/负对照，零模型调用，同时防止批量 iOS 一致性反馈漏路由和普通鸿蒙任务误触发 V2。

## 使用

只校验数据集和评分器，不调用模型：

```bash
python3 evals/scripts/self_test.py
python3 evals/scripts/run_eval.py validate
python3 evals/scripts/run_eval.py list --suite quick
```

执行单个快速用例会调用 `codex exec --json` 并消耗模型用量：

```bash
python3 evals/scripts/run_eval.py run \
  --case ios-quick-d1-nil-guard \
  --framework-version 2.5.0 \
  --model gpt-5.6-terra \
  --reasoning low
```

对发布候选做一次独立质量盲审会产生第二次模型调用，因此默认不运行：

```bash
python3 evals/scripts/judge_quality.py \
  --run evals/artifacts/runs/2.4.1/某次运行/run.json \
  --model gpt-6-astra --reasoning low
python3 evals/scripts/run_eval.py grade \
  --run evals/artifacts/runs/2.4.1/某次运行/run.json
```

批量运行必须显式增加 `--execute-all`，避免误消耗 Token：

```bash
python3 evals/scripts/run_eval.py run-suite \
  --suite quick --execute-all \
  --framework-version 2.5.0 \
  --model gpt-5.6-terra --reasoning low
```

比较两个版本：

```bash
python3 evals/scripts/compare_results.py \
  --baseline evals/artifacts/runs/2.3.0 \
  --candidate evals/artifacts/runs/2.4.1 \
  --output evals/artifacts/comparisons/2.4.0-vs-2.4.1
```

## 计量原则

- 相同用例必须固定模型、reasoning、Codex CLI、项目 revision 和环境，只改变框架版本。
- 精确 Token 只接受 JSONL 轨迹中的 usage；没有 usage 时写 `available: false`。
- 同时记录总输入、缓存输入和非缓存输入；版本对比优先关注 `uncachedInputTokens`，但仍保留平台返回的总量。
- `traceBytes`、命令数、工具调用数和输出长度只是上下文代理指标，不能称为 Token。
- 编译、模拟器、测试和报告次数来自真实命令轨迹。
- 修改范围来自隔离工作区的 `git status --porcelain`。
- 确定性检查负责正确性、分级、命令和文件范围；代码结构、注释、UI/交互等主观质量另做盲审，不能用同一个执行 Agent 自评替代。
- `run-suite` 不带 `--execute-all` 会拒绝执行，防止一次误触发 12 个模型任务。

产物写入 `evals/artifacts/`，该目录不进入发布包中的历史结果。发布包只包含评测定义、脚本和 Fixture。

## 2.6 可执行夹具与真实版本

新增 `run_eval.py fixture-check` 零模型真实演练全部 quick 接入，显式创建仅限合成目录的授权；修正旧发现基线路径。`run --dry-run` 不调用模型，实际版本参数必须与所执行套件相同。比较要求相同模型/推理/CLI/环境/用例/输入合同和真实套件摘要，缺少合同的旧结果只能查看。`policy_benchmark.py` 配合固定 `policy-routing.json` 只衡量确定性分级，零项目读取不代表实际工作流零成本。`safety_regression.py` 包含伪 UI 证据、过期绑定、配置风险、读取排除、更新回滚与固定版本漂移反例。Token 缺失写未测，不据此承诺省量。
