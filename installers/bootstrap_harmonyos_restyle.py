#!/usr/bin/env python3
"""显式安装独立马甲包 Skill；仅新目标，绝不安装或转换日常框架。"""
import argparse
import json
from pathlib import Path
from plan_harmonyos_restyle import plan
from enable_harmonyos_reimplementation import enable
from project_framework import installation_locks, LOCK_PATHS
from read_boundary import ReadBoundary

def bootstrap(project, source, apply=False, expected_plan=None, offline_ack=''):
    initial=plan(project,source)
    if initial['conflicts']:
        raise ValueError('已有项目控制上下文；需要正式交接，不覆盖或卸载')
    boundary=ReadBoundary(project)
    for name in LOCK_PATHS: boundary.require(name)
    if not apply:
        return {'status':'dry-run','plan':initial,'writes':[], 'nextStep':'核对源/目标及授权并停止所有写入者，再用本次 planSha256 显式安装 Skill。'}
    if expected_plan != initial['planSha256'] or not isinstance(offline_ack,str) or not offline_ack.strip():
        raise ValueError('需要当前精确计划摘要和真实停写确认来源；确认文字不代替实际停写')
    def revalidate():
        current=plan(project,source)
        if current['conflicts'] or current['planSha256'] != initial['planSha256']:
            raise ValueError('安装期间计划、授权、身份或控制上下文变化')
    with installation_locks(project):
        revalidate()
        result=enable(project,source,True,before_publish=revalidate,standalone=True)
    result.update(workflow='skill-only',phase='rebrand-production-before-acceptance',
                  businessExecutionPerformed=False,acceptanceRecorded=False,dailyFrameworkInstalled=False)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',required=True,type=Path);p.add_argument('--source',required=True,type=Path)
    p.add_argument('--apply',action='store_true');p.add_argument('--plan-sha256');p.add_argument('--offline-writers-stopped',default='')
    a=p.parse_args()
    try: print(json.dumps(bootstrap(a.project,a.source,a.apply,a.plan_sha256,a.offline_writers_stopped),ensure_ascii=False,indent=2))
    except (ValueError,OSError,TypeError) as e:
        print(json.dumps({'status':'blocked','reason':str(e)},ensure_ascii=False));return 2
    return 0
if __name__=='__main__': raise SystemExit(main())
