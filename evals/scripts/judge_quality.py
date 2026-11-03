#!/usr/bin/env python3
"""Optional model judge for release-candidate quality review.

This is deliberately separate from run_eval.py so quick development checks do not
silently double model usage.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from eval_common import EVAL_ROOT, find_case, read_json, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="run.json 路径")
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning", default="low", choices=("low", "medium", "high", "xhigh"))
    parser.add_argument("--codex")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    run_path = Path(args.run).expanduser().resolve()
    run = read_json(run_path)
    case = find_case(run["caseId"])
    diff_path = run_path.with_name("changes.diff")
    if not diff_path.exists():
        print(f"ERROR: 缺少 {diff_path}", file=sys.stderr)
        return 1
    codex = args.codex or shutil.which("codex")
    if not codex:
        print("ERROR: 未找到 codex CLI", file=sys.stderr)
        return 1

    payload = {
        "case": {"id": case["id"], "prompt": case["prompt"], "expected": case["expected"]},
        "agentResult": run.get("result"),
        "diff": diff_path.read_text(encoding="utf-8", errors="replace"),
    }
    prompt = (
        "你是独立盲审员，只依据下面的任务、执行结果和 diff 评分。"
        "逐项检查 correctness/readability/structure/comments/extensibility/scope/ui_interaction；"
        "不适用项应给出保守通过分并在 evidence 说明。不要修改文件，不把执行者自述当成证据。\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    quality_path = run_path.with_name("quality.json")
    trace_path = run_path.with_name("quality-trace.jsonl")
    stderr_path = run_path.with_name("quality-stderr.log")
    command = [
        codex,
        "exec",
        "--json",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--model",
        args.model,
        "-c",
        f'model_reasoning_effort="{args.reasoning}"',
        "--output-schema",
        str(EVAL_ROOT / "schemas" / "quality-rubric.schema.json"),
        "-o",
        str(quality_path),
        prompt,
    ]
    with trace_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open("w", encoding="utf-8") as stderr_file:
        result = subprocess.run(
            command,
            cwd=run_path.parent,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            timeout=args.timeout,
        )
    if result.returncode != 0 or not quality_path.exists():
        print(f"ERROR: Judge 失败，exit={result.returncode}，见 {stderr_path}", file=sys.stderr)
        return 2
    run["quality"] = read_json(quality_path)
    write_json(run_path, run)
    print(quality_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
