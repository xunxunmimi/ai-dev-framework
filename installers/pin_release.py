#!/usr/bin/env python3
"""Create or verify a content-addressed framework snapshot; no registration."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'shared/task-routing'))
from task_runtime import suite_digest


def inventory(source: Path) -> dict:
    """只包含活动框架源码白名单；不打包项目、凭据、会话、发布物或历史证据。"""
    names = ['VERSION', 'README.md']
    allowed = {'.py','.sh','.md','.json','.json5','.yaml','.yml','.swift','.ets','.pbxproj','.gitignore'}
    for folder in ('frameworks','shared','installers','capabilities'):
        names += [p.relative_to(source).as_posix() for p in (source/folder).rglob('*') if p.is_file() and not p.is_symlink() and p.suffix in allowed and not any(x.startswith('.') or x=='__pycache__' for x in p.relative_to(source).parts)]
    return {name:{'sha256':hashlib.sha256((source/name).read_bytes()).hexdigest(), 'mode':(source/name).stat().st_mode & 0o777} for name in sorted(names)}


def verify(root: Path) -> dict:
    """校验固定快照内容；可变路径名不能替代内容身份。"""
    record=json.loads((root/'.framework-pin.json').read_text())
    current=inventory(root)
    if current != record['files'] or suite_digest(root) != record['suiteSha256']:
        raise ValueError('pinned release drift; refuse registration/reuse')
    return record


def pin(source: Path, destination: Path, apply: bool = False) -> dict:
    """按版本和内容创建新目录；已存在只校验，不覆盖。"""
    files=inventory(source)
    record={'version':(source/'VERSION').read_text().strip(), 'suiteSha256':suite_digest(source), 'files':files}
    root=destination/f"{record['version']}-{record['suiteSha256'][:16]}"
    if root.exists():
        if verify(root) != record:raise ValueError('existing pin differs')
        return {'status':'verified','root':str(root),**record}
    if not apply:return {'status':'dry-run','root':str(root),'fileCount':len(files),'suiteSha256':record['suiteSha256'],'writes':[]}
    destination.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.pin-',dir=destination))
    try:
        for name,item in files.items():
            p=stage/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/name,p);p.chmod(item['mode'])
        (stage/'.framework-pin.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
        if inventory(stage) != files or suite_digest(stage) != record['suiteSha256']:raise ValueError('source changed during pin')
        os.rename(stage,root)
    finally:
        if stage.exists():shutil.rmtree(stage)
    return {'status':'created','root':str(root),**record}


def main() -> int:
    """预览默认无写入；固定快照不修改任何技能注册。"""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--verify',type=Path)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--path-only',action='store_true')
    args=parser.parse_args()
    if args.verify:result=verify(args.verify)
    else:
        if not args.destination:raise ValueError('--destination required')
        result=pin(args.source.resolve(),args.destination,args.apply)
    print(result['root'] if args.path_only else json.dumps(result,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
