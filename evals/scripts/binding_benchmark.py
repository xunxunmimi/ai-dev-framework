#!/usr/bin/env python3
"""Measure actual Python read opens and elapsed time for a bound fixture check."""
from __future__ import annotations
import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main() -> int:
    """同一合成项目与 binding 操作；不衡量模型或业务端到端耗时。"""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();suite=args.suite.resolve()
    sys.path.insert(0,str(suite/'shared/task-routing'))
    from task_runtime import observe_identity, atomic_json, begin, binding
    with tempfile.TemporaryDirectory(prefix='binding-benchmark-') as temporary:
        root=Path(temporary).resolve()
        (root/'Package.swift').write_text('// same non-buildable benchmark identity marker')
        (root/'one.swift').write_text('// fixed task file')
        atomic_json(root/'.ai-project/read-policy.json',{'schemaVersion':'1.0','confirmed':True,'scopeKind':'project','projectRoot':str(root),'mode':'complete','allow':['**'],'deny':[],'scopeDescription':'owned synthetic benchmark only'})
        subprocess.run(['bash',str(suite/'installers/setup-project.sh'),'ios-daily',str(root)],check=True,capture_output=True)
        atomic_json(root/'.ai-project/identity.json',observe_identity(root,'ios'))
        begin(root,'benchmark',['one.swift'],'fixed binding operation')
        active=False;reads=[];commands=[]
        def audit(event, values):
            """仅统计激活区间的 Python 只读 open 与子进程；不跟踪用户数据。"""
            if not active:return
            if event=='subprocess.Popen':commands.append(str(values[0]))
            if event!='open' or not isinstance(values[0],(str,bytes,os.PathLike)):return
            flags=values[2]
            if flags & os.O_ACCMODE != os.O_RDONLY:return
            path=Path(os.fsdecode(values[0])).absolute()
            if path.is_relative_to(root) or path.is_relative_to(suite):
                try:reads.append({'path':str(path),'bytes':path.stat().st_size})
                except OSError:pass
        sys.addaudithook(audit)
        samples=[]
        for _ in range(5):
            reads=[];commands=[];active=True;started=time.perf_counter()
            binding(root,'benchmark','D2')
            duration=time.perf_counter()-started;active=False
            samples.append({'seconds':duration,'pythonReadOpenCalls':len(reads),'pythonReadFileBytes':sum(x['bytes'] for x in reads),'subprocessCalls':len(commands)})
    report={'suite':str(suite),'version':(suite/'VERSION').read_text().strip(),'operation':'one binding on fixed owned synthetic Package.swift project; 5 warm in-process samples','limitations':'Python read-open file sizes; OS caching and git child internal reads not measured; not workflow/model latency','tokenUsage':{'available':False,'reason':'no model invoked'},'samples':samples,'median':{k:statistics.median(x[k] for x in samples) for k in samples[0]}}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report['median']));return 0


if __name__=='__main__':raise SystemExit(main())
