#!/usr/bin/env python3
"""Decide eligibility from explicit project selection; never edit UI or enable a skill."""
from __future__ import annotations
import argparse,json,os,stat
from pathlib import Path,PurePosixPath

INTENTS={'new-ui','optimize-ui'}

def relative_file(value: str) -> str:
    """只接受精确项目相对文件；范围不能使用目录通配或控制/凭据路径。"""
    if not isinstance(value,str) or not value or '\\' in value or '\0' in value:
        raise ValueError('invalid file scope')
    path=PurePosixPath(value)
    if path.is_absolute() or str(path)!=value or '..' in path.parts or value=='.' or any(c in value for c in '*?['):
        raise ValueError('file scope must be an exact relative path')
    if any(p.startswith('.') for p in path.parts) or set(p.casefold() for p in path.parts)&{'credentials','secrets','pods','vendor','node_modules'}:
        raise ValueError('control, credential and third-party files are outside UI scope')
    return value

def decide(selection,project: str,intent: str,design_status: str,files: list[str]) -> dict:
    """项目未选择、上下文未知或超范围均关闭；已有设计取两次事实中更保守者。"""
    def off(reason):return {'eligible':False,'reason':reason,'mode':'off','mayRedesign':False}
    if selection is None:return off('project-not-selected')
    if not isinstance(selection,dict) or selection.get('schemaVersion')!='1.0' or selection.get('enabled') is not True:
        return off('disabled-or-invalid-selection')
    if selection.get('projectRoot')!=project:return off('selection-belongs-to-another-project')
    if selection.get('intent') not in INTENTS or intent!=selection['intent']:return off('current-task-not-selected')
    if selection.get('designStatus') not in {'none','existing'} or design_status not in {'none','existing'}:
        return off('design-context-required')
    selected=selection.get('files')
    try:
        if not isinstance(selected,list) or not selected or not isinstance(files,list) or not files:return off('file-scope-required')
        allowed={relative_file(p) for p in selected};requested={relative_file(p) for p in files}
    except ValueError:return off('invalid-file-scope')
    if not requested.issubset(allowed):return off('outside-selected-files')
    existing=selection['designStatus']=='existing' or design_status=='existing'
    if existing and intent=='new-ui':return off('existing-design-takes-priority')
    mode='design-fidelity-only' if existing else ('new-ui-direction' if intent=='new-ui' else 'audit-and-polish')
    return {'eligible':True,'reason':'explicit-project-and-task-selection','mode':mode,'mayRedesign':not existing and intent=='new-ui','files':sorted(requested)}

def load_selection(skill: Path,project: Path):
    """仅从项目内标准技能目录读取选择记录；逐级拒绝链接，不读取项目源码。"""
    expected=project/'.agents/skills/unslop-ui'
    if skill!=expected:return None
    cursor=project
    if project.resolve()!=project:raise ValueError('project path contains a symlink')
    for part in ('.agents','skills','unslop-ui'):
        cursor=cursor/part
        if cursor.is_symlink():raise ValueError('skill path contains a symlink')
    path=skill/'selection.json'
    try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return None
    with os.fdopen(fd,'r',encoding='utf-8') as handle:
        meta=os.fstat(handle.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_nlink!=1 or meta.st_size>65536:raise ValueError('selection must be a small unlinked regular file')
        return json.load(handle)

def main() -> int:
    """缺少当前明确 UI 意图或设计事实时默认关闭；退出码 2 表示未获资格。"""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--intent',choices=['none',*sorted(INTENTS)],default='none')
    parser.add_argument('--design-status',choices=['unknown','none','existing'],default='unknown')
    parser.add_argument('--file',action='append',default=[])
    args=parser.parse_args();root=args.project.absolute()
    try:
        result=decide(load_selection(Path(__file__).absolute().parents[1],root),str(root),args.intent,args.design_status,args.file)
    except (ValueError,OSError,TypeError) as error:
        result={'eligible':False,'mode':'off','mayRedesign':False,'reason':str(error)}
    print(json.dumps(result,ensure_ascii=False));return 0 if result['eligible'] else 2

if __name__=='__main__':raise SystemExit(main())
