#!/usr/bin/env python3
"""Reproducible zero-model policy counterexamples; never a model token benchmark."""
from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    """用同一冻结用例集载入指定套件，记录真实分级及进程内耗时。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, required=True)
    parser.add_argument('--cases', type=Path, default=Path(__file__).resolve().parents[1] / 'cases/policy-routing.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.suite.resolve()
    sys.path.insert(0, str(root / 'shared/task-routing'))
    from task_router import classify
    results = []
    for case in json.loads(args.cases.read_text()):
        started = time.perf_counter()
        result = classify(case['request'], case['files'], stage='final')
        duration = time.perf_counter() - started
        checks = result.get('verificationRequirements', {}).get('required')
        missing = [x for x in case.get('requiredChecks', []) if checks is not None and x not in checks]
        release = result.get('verificationRequirements', {}).get('releaseBuildRequired')
        passed = result['impactLevel'] == case['expected'] and not missing and ('releaseRequired' not in case or release is None or release == case['releaseRequired'])
        results.append({'caseId': case['id'], 'expected': case['expected'], 'actual': result['impactLevel'], 'pass': passed, 'missingRequiredChecks': missing if checks is not None else None, 'checkContractAvailable': checks is not None, 'releaseBuildRequired': release, 'seconds': duration, 'projectFileReads': 0, 'projectBytesRead': 0, 'modelCalls': 0})
    report = {'schemaVersion': '1.0', 'suite': str(root), 'version': (root / 'VERSION').read_text().strip(), 'routerSha256': hashlib.sha256((root / 'shared/task-routing/task_router.py').read_bytes()).hexdigest(), 'caseSha256': hashlib.sha256(args.cases.read_bytes()).hexdigest(), 'scope': 'pure deterministic route function only; no business execution, workflow latency or model token savings measured', 'tokenUsage': {'available': False, 'reason': 'no model invocation'}, 'accuracy': sum(x['pass'] for x in results) / len(results), 'results': results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'version': report['version'], 'cases': len(results), 'passed': sum(x['pass'] for x in results), 'output': str(args.output)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
