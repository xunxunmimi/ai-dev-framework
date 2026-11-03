#!/usr/bin/env python3
"""Preview or explicitly copy optional UI capability into one selected Codex project."""
from __future__ import annotations
import argparse,hashlib,json,os,shutil,sys,tempfile
from pathlib import Path
SUITE=Path(__file__).resolve().parents[1]
CAPABILITY=SUITE/'capabilities/unslop-ui'
sys.path.insert(0,str(SUITE/'shared/task-routing'))
sys.path.insert(0,str(CAPABILITY/'scripts'))
from read_boundary import ReadBoundary
from task_runtime import lock
from check_ui_scope import decide,relative_file
TARGET='.agents/skills/unslop-ui'

def enable(project: Path,intent: str,design_status: str,files: list[str],apply: bool=False) -> dict:
    """默认只预览；一次目录发布，不覆盖已有技能，不改框架绑定或其他项目。"""
    root=project.absolute()
    if not root.is_dir() or root.resolve()!=root or root in {Path(root.anchor),Path.home(),SUITE}:
        raise ValueError('select a real project directory, not a global or suite directory')
    boundary=ReadBoundary(root)
    selection={'schemaVersion':'1.0','enabled':True,'projectRoot':str(root),'intent':intent,'designStatus':design_status,'files':sorted({relative_file(p) for p in files})}
    eligibility=decide(selection,str(root),intent,design_status,files)
    if not eligibility['eligible']:raise ValueError(eligibility['reason'])
    boundary.require(TARGET)
    if boundary.exists(TARGET):raise ValueError('project skill already exists; inspect it rather than overwrite')
    source={}
    for p in sorted(CAPABILITY.rglob('*')):
        if p.is_symlink():raise ValueError('symlink in capability source')
        if p.is_file() and '__pycache__' not in p.parts and p.name!='.DS_Store':
            name=p.relative_to(CAPABILITY).as_posix();boundary.require(TARGET+'/'+name)
            source[name]=hashlib.sha256(p.read_bytes()).hexdigest()
    boundary.require(TARGET+'/selection.json')
    out={'status':'dry-run','project':str(root),'destination':str(root/TARGET),'selection':selection,'files':sorted(source),'writes':[]}
    if not apply:return out
    boundary.require('.ai-project/optional-ui.lock')
    with lock(root/'.ai-project/optional-ui.lock'):
        if boundary.exists(TARGET):raise ValueError('project skill appeared; refuse overwrite')
        for name in ['.agents','.agents/skills']:
            boundary.require(name)
            if boundary.exists(name):
                if not (root/name).is_dir():raise ValueError('skill parent is not a directory')
            else:(root/name).mkdir()
        with tempfile.TemporaryDirectory(prefix='.unslop-ui-',dir=root/'.agents/skills') as folder:
            stage=Path(folder)/'payload';shutil.copytree(CAPABILITY,stage,ignore=shutil.ignore_patterns('__pycache__','.DS_Store'))
            for name,digest in source.items():
                if hashlib.sha256((stage/name).read_bytes()).hexdigest()!=digest:raise ValueError('source changed during copy')
            (stage/'selection.json').write_text(json.dumps(selection,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            os.rename(stage,root/TARGET)
    out.update(status='enabled',writes=[TARGET+'/'+name for name in sorted(source)]+[TARGET+'/selection.json'])
    return out

def main() -> int:
    """仅支持项目内 Codex 安装；不存在全局模式或自动升级入口。"""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--intent',choices=['new-ui','optimize-ui'],required=True)
    parser.add_argument('--design-status',choices=['none','existing'],required=True)
    parser.add_argument('--file',action='append',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    try:result=enable(args.project,args.intent,args.design_status,args.file,args.apply)
    except (ValueError,OSError,TypeError) as error:
        print(json.dumps({'status':'blocked','reason':str(error)},ensure_ascii=False));return 2
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
