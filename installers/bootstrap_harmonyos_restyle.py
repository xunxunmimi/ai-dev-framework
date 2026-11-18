#!/usr/bin/env python3
"""显式安装独立马甲包 Skill；仅新目标，绝不安装或转换日常框架。"""
import argparse
import json
from pathlib import Path
from plan_harmonyos_restyle import plan
from enable_harmonyos_reimplementation import enable, CAPABILITY, TARGET, INSTALL_PARENTS, INSTALL_LOCK
from project_framework import installation_locks, LOCK_PATHS
from read_boundary import ReadBoundary

def required_read_scope():
    """仅检查当前可信发行包，不读取目标；这是待所有者审阅的范围，不是授权。"""
    names={'.ai-project','.agents','.agents/skills',TARGET,INSTALL_LOCK,*LOCK_PATHS,
      '.ai-project/framework-installation.json','.ai-project/lifecycle.json','.codex-rules',
      '.migration-v2','AGENTS.md','.ai-project/identity.json','.ai-project/operation-holds.json',
      '.ai-project/legacy-baseline.json','.ai-project/task-input.json','.ai-project/tasks',
      '.ai-project/upgrades','.ai-project/operation-holds','.ai-project/checkpoints'}
    for p in CAPABILITY.rglob('*'):
        if p.is_symlink():raise ValueError('capability link')
        if '__pycache__' in p.parts or p.name=='.DS_Store':continue
        names.add(TARGET+'/'+p.relative_to(CAPABILITY).as_posix())
    names.add(TARGET+'/selection.json')
    return {'status':'scope-proposal-only','targetReadAllowCandidates':sorted(names),
      'parentInspections':list(INSTALL_PARENTS),'installationLock':INSTALL_LOCK,
      'sourceRequirement':'源项目须有独立已确认读取策略；此清单不授予源读取或任何写权限。',
      'writes':[],'policyEdited':False,'authorizationGranted':False,
      'nextStep':'首次确认读取策略时一并审阅这些精确路径。已有策略缺项或明确拒绝时，由策略所有者按正式流程处理；不得自动追加、覆盖拒绝或扩大为 .agents/**。范围就绪后运行正常预检，显式 apply 会创建缺失且已获准的父目录。'}

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
    p.add_argument('--project',type=Path);p.add_argument('--source',type=Path)
    p.add_argument('--print-required-read-scope',action='store_true',help='只列首次策略确认的精确路径，不访问源/目标或创建目录')
    p.add_argument('--apply',action='store_true');p.add_argument('--plan-sha256');p.add_argument('--offline-writers-stopped',default='')
    a=p.parse_args()
    if a.print_required_read_scope and (a.apply or a.plan_sha256 or a.offline_writers_stopped):
        p.error('--print-required-read-scope cannot be combined with installation flags')
    if not a.print_required_read_scope and (a.project is None or a.source is None):
        p.error('--project and --source are required for installation or preflight')
    try: print(json.dumps(required_read_scope() if a.print_required_read_scope else bootstrap(a.project,a.source,a.apply,a.plan_sha256,a.offline_writers_stopped),ensure_ascii=False,indent=2))
    except (ValueError,OSError,TypeError) as e:
        print(json.dumps({'status':'blocked','reason':str(e)},ensure_ascii=False));return 2
    return 0
if __name__=='__main__': raise SystemExit(main())
