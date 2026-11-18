#!/usr/bin/env python3
"""Preview or explicitly install the HarmonyOS restyling skill in one selected Codex project."""
from __future__ import annotations
import argparse,hashlib,json,os,shutil,sys,tempfile,stat
from pathlib import Path
SUITE=Path(__file__).resolve().parents[1]
CAPABILITY=SUITE/'capabilities/harmonyos-app-reimplementation'
sys.path.insert(0,str(SUITE/'shared/task-routing'))
sys.path.insert(0,str(CAPABILITY/'scripts'))
from read_boundary import ReadBoundary
from task_runtime import project_lock
from read_boundary import secure_fd
TARGET='.agents/skills/harmonyos-app-reimplementation'
INSTALL_PARENTS=('.agents','.agents/skills')
INSTALL_LOCK='.ai-project/reimplementation-install.lock'

def inspect_install_parents(root, boundary):
    result={}
    for name in (*INSTALL_PARENTS,INSTALL_LOCK):
        try:boundary.require(name)
        except ValueError as error:
            raise ValueError('安装前置路径未授权或被明确拒绝: '+name+'。这不表示目录不存在；不要手工创建绕过。先运行 bootstrap_harmonyos_restyle.py --print-required-read-scope 交由策略所有者复核；本工具不会修改已有策略。') from error
        if name==INSTALL_LOCK:continue
        try:fd=secure_fd(root,name,directory=True)
        except FileNotFoundError:result[name]='absent-will-create-on-explicit-apply'
        else:os.close(fd);result[name]='present'
    return result

def enable(project: Path, source: Path, apply: bool=False, *, before_publish=None, standalone: bool=False) -> dict:
    """源只读、目录互不包含；只新增显式选择的目标技能，不修改既有主框架。"""
    root=project.absolute()
    original=source.absolute()
    for folder in (root,original):
        if not folder.is_dir() or folder.resolve()!=folder or folder in {Path(folder.anchor),Path.home(),SUITE}:
            raise ValueError('select canonical real project directories, not global or suite directories')
    if root == original or root in original.parents or original in root.parents:
        raise ValueError('source and target must be separate, non-nested directories')
    boundary=ReadBoundary(root)
    source_boundary=ReadBoundary(original)
    selection={'schemaVersion':'1.0','enabled':True,'projectRoot':str(root),'sourceRoot':str(original),
               'intent':'implementation-preserving-restyle','sourceReadOnly':True,
               'sourcePolicySha256':source_boundary.sha256,'targetPolicySha256':boundary.sha256}
    if standalone:
        selection['workflowPhase']='rebrand-production-before-acceptance'
        selection['dailyHandoff']='explicit-after-acceptance'
    inspect_install_parents(root,boundary)
    boundary.require(TARGET)
    if boundary.exists(TARGET):raise ValueError('project skill already exists; inspect it rather than overwrite')
    source={}
    for p in sorted(CAPABILITY.rglob('*')):
        if p.is_symlink():raise ValueError('symlink in capability source')
        if not p.is_dir() and (not p.is_file() or p.stat().st_nlink != 1):raise ValueError('unsafe capability source file')
        if p.is_file() and '__pycache__' not in p.parts and p.name!='.DS_Store':
            name=p.relative_to(CAPABILITY).as_posix();boundary.require(TARGET+'/'+name)
            source[name]=hashlib.sha256(p.read_bytes()).hexdigest()
    boundary.require(TARGET+'/selection.json')
    # A successful preview must check every authorization needed by apply, without creating anything.
    for name in (*INSTALL_PARENTS, INSTALL_LOCK):
        boundary.require(name)
    out={'status':'dry-run','project':str(root),'destination':str(root/TARGET),'selection':selection,'files':sorted(source),'parentDirectoriesEnsuredOnApply':list(INSTALL_PARENTS),'writes':[]}
    if not apply:return out
    with project_lock(root,INSTALL_LOCK):
        if before_publish is not None: before_publish()
        if boundary.exists(TARGET):raise ValueError('project skill appeared; refuse overwrite')
        for name in INSTALL_PARENTS:
            boundary.require(name)
            try:
                descriptor=secure_fd(root,name,directory=True)
            except FileNotFoundError:
                (root/name).mkdir()
            else:
                os.close(descriptor)
        with tempfile.TemporaryDirectory(prefix='.reimplementation-',dir=root/'.agents/skills') as folder:
            stage=Path(folder)/'payload';shutil.copytree(CAPABILITY,stage,ignore=shutil.ignore_patterns('__pycache__','.DS_Store'))
            staged={}
            for item in stage.rglob('*'):
                metadata=item.lstat()
                if stat.S_ISLNK(metadata.st_mode):raise ValueError('symlink in copied capability')
                if stat.S_ISDIR(metadata.st_mode):continue
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:raise ValueError('unsafe copied capability file')
                staged[item.relative_to(stage).as_posix()]=hashlib.sha256(item.read_bytes()).hexdigest()
            if staged != source:raise ValueError('source changed during copy')
            for name,digest in source.items():
                if hashlib.sha256((stage/name).read_bytes()).hexdigest()!=digest:raise ValueError('source changed during copy')
            (stage/'selection.json').write_text(json.dumps(selection,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            if before_publish is not None: before_publish()
            os.rename(stage,root/TARGET)
    out.update(status='enabled',writes=[TARGET+'/'+name for name in sorted(source)]+[TARGET+'/selection.json'])
    return out

def main() -> int:
    """仅支持项目内 Codex 安装；不存在全局模式或自动升级入口。"""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    try:result=enable(args.project,args.source,args.apply)
    except (ValueError,OSError,TypeError) as error:
        print(json.dumps({'status':'blocked','reason':str(error)},ensure_ascii=False));return 2
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
