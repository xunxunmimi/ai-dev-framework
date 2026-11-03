#!/usr/bin/env python3
"""Compare paired evaluation results from two framework versions."""

from __future__ import annotations

import argparse
import statistics
from pathlib import Path
from typing import Any

from eval_common import read_json, write_json


def latest_scores(root: Path) -> dict[str, dict[str, Any]]:
    selected: dict[str, tuple[float, dict[str, Any]]] = {}
    if not root.exists():
        return {}
    for path in root.rglob("score.json"):
        score = read_json(path)
        case_id = score.get("caseId")
        if not case_id:
            continue
        stamp = path.stat().st_mtime
        previous = selected.get(case_id)
        if previous is None or stamp > previous[0]:
            selected[case_id] = (stamp, score)
    return {case_id: value[1] for case_id, value in selected.items()}


def median(values: list[float | int]) -> float | None:
    return round(float(statistics.median(values)), 2) if values else None


def percent_change(baseline: float | None, candidate: float | None) -> float | None:
    if baseline in (None, 0) or candidate is None:
        return None
    return round(100 * (candidate - baseline) / baseline, 2)


def summarize(scores: list[dict[str, Any]]) -> dict[str, Any]:
    def metric(name: str) -> list[float | int]:
        return [score["metrics"][name] for score in scores if score.get("metrics", {}).get(name) is not None]

    usages = [score["metrics"].get("tokenUsage") for score in scores]
    usages = [usage for usage in usages if usage]
    quality = [score.get("quality", {}).get("score") for score in scores if score.get("quality")]
    return {
        "cases": len(scores),
        "passRate": round(sum(bool(score.get("pass")) for score in scores) / len(scores), 4) if scores else None,
        "deterministicScoreMedian": median([score["deterministicScore"] for score in scores]),
        "unrelatedChangedFilesMedian": median(metric("unrelatedChangedFiles")),
        "buildCommandsMedian": median(metric("buildCommands")),
        "testCommandsMedian": median(metric("testCommands")),
        "simulatorCommandsMedian": median(metric("simulatorCommands")),
        "durationSecondsMedian": median(metric("durationSeconds")),
        "qualityScoreMedian": median(quality),
        "tokenCases": len(usages),
        "inputTokensMedian": median([usage["inputTokens"] for usage in usages if "inputTokens" in usage]),
        "uncachedInputTokensMedian": median([usage["uncachedInputTokens"] for usage in usages if "uncachedInputTokens" in usage]),
        "outputTokensMedian": median([usage["outputTokens"] for usage in usages if "outputTokens" in usage]),
        "totalTokensMedian": median([usage["totalTokens"] for usage in usages if "totalTokens" in usage]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    baseline_root = Path(args.baseline).expanduser().resolve()
    candidate_root = Path(args.candidate).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    baseline = latest_scores(baseline_root)
    candidate = latest_scores(candidate_root)
    paired_ids = sorted(set(baseline) & set(candidate))
    baseline_summary = summarize([baseline[case_id] for case_id in paired_ids])
    candidate_summary = summarize([candidate[case_id] for case_id in paired_ids])

    regressions: list[dict[str, Any]] = []
    for case_id in paired_ids:
        before, after = baseline[case_id], candidate[case_id]
        if not before.get('comparisonContract') or before.get('comparisonContract') != after.get('comparisonContract'):
            raise ValueError(f'{case_id}: missing or mismatched model/case/fixture/environment contract; comparison rejected')
        if not before.get('suiteIdentity') or not after.get('suiteIdentity'):
            raise ValueError(f'{case_id}: actual suite identity missing; historical labels are not reproducible baselines')
        reasons: list[str] = []
        if before.get("pass") and not after.get("pass"):
            reasons.append("pass→fail")
        for metric in ("unrelatedChangedFiles", "buildCommands", "testCommands", "simulatorCommands"):
            if after["metrics"].get(metric, 0) > before["metrics"].get(metric, 0):
                reasons.append(f"{metric} 增加")
        before_quality = (before.get("quality") or {}).get("score")
        after_quality = (after.get("quality") or {}).get("score")
        if before_quality is not None and after_quality is not None and after_quality < before_quality:
            reasons.append("qualityScore 降低")
        if reasons:
            regressions.append({"caseId": case_id, "reasons": reasons})

    comparison = {
        "schemaVersion": "1.0",
        "pairedCases": len(paired_ids),
        "unpairedBaseline": sorted(set(baseline) - set(candidate)),
        "unpairedCandidate": sorted(set(candidate) - set(baseline)),
        "baseline": baseline_summary,
        "candidate": candidate_summary,
        "changesPercent": {
            key: percent_change(baseline_summary.get(key), candidate_summary.get(key))
            for key in (
                "passRate",
                "deterministicScoreMedian",
                "unrelatedChangedFilesMedian",
                "buildCommandsMedian",
                "testCommandsMedian",
                "simulatorCommandsMedian",
                "durationSecondsMedian",
                "qualityScoreMedian",
                "inputTokensMedian",
                "uncachedInputTokensMedian",
                "outputTokensMedian",
                "totalTokensMedian",
            )
        },
        "regressions": regressions,
    }
    write_json(output / "comparison.json", comparison)
    lines = [
        "# Eval 版本对比",
        "",
        f"- 配对用例：{len(paired_ids)}",
        f"- 基线通过率：{baseline_summary['passRate']}",
        f"- 候选通过率：{candidate_summary['passRate']}",
        f"- 基线 Token 可用用例：{baseline_summary['tokenCases']}",
        f"- 候选 Token 可用用例：{candidate_summary['tokenCases']}",
        f"- 总 Token 中位数变化：{comparison['changesPercent']['totalTokensMedian']}",
        f"- 回归数：{len(regressions)}",
        "",
        "Token 数据缺失时保持 unavailable，不使用字符数估算。",
    ]
    if regressions:
        lines.extend(["", "## 回归"])
        lines.extend(f"- {item['caseId']}: {', '.join(item['reasons'])}" for item in regressions)
    (output / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output / "comparison.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
