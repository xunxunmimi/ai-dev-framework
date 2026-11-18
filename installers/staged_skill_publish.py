"""发布已核完整暂存 Skill 的窄原语。无 CLI、无授权生成；调用方须协调全部写者。"""
import ctypes
import errno
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'shared/task-routing'))
from read_boundary import ReadBoundary, secure_fd

PARENT='.agents/skills'
DEST='harmonyos-app-reimplementation'
MAX_FILE=4*1024*1024

def _identity(fd):
    s=os.fstat(fd);return s.st_dev,s.st_ino

def _rename_exclusive(parent_fd,stage):
    """平台内核原子 no-replace；不支持时阻断，绝不降级为 os.rename。"""
    lib=ctypes.CDLL(None,use_errno=True)
    if sys.platform=='linux' and hasattr(lib,'renameat2'):
        fn=lib.renameat2;flag=1
    elif sys.platform=='darwin' and hasattr(lib,'renameatx_np'):
        fn=lib.renameatx_np;flag=4  # RENAME_EXCL
    else:raise RuntimeError('atomic no-clobber directory publication unsupported')
    fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];fn.restype=ctypes.c_int
    if fn(parent_fd,os.fsencode(stage),parent_fd,os.fsencode(DEST),flag)!=0:
        number=ctypes.get_errno();raise OSError(number,os.strerror(number),DEST)

def _verify(fd,expected,require):
    actual={};directories=[]
    def walk(dfd,prefix):
        for name in sorted(os.listdir(dfd)):
            if name in ('.','..') or '/' in name:raise ValueError('unsafe staged name')
            relative=prefix+name;require(relative);s=os.stat(name,dir_fd=dfd,follow_symlinks=False)
            if stat.S_ISDIR(s.st_mode):
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=dfd)
                try:directories.append(relative);walk(child,relative+'/');os.fsync(child)
                finally:os.close(child)
            elif stat.S_ISREG(s.st_mode) and s.st_nlink==1:
                child=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=dfd)
                try:
                    before=os.fstat(child)
                    if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>MAX_FILE:raise ValueError('unsafe staged file')
                    h=hashlib.sha256();size=0
                    while True:
                        chunk=os.read(child,65536)
                        if not chunk:break
                        size+=len(chunk)
                        if size>MAX_FILE:raise ValueError('staged file too large')
                        h.update(chunk)
                    after=os.fstat(child)
                    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise ValueError('stage changed while checking')
                    os.fsync(child);actual[relative]=h.hexdigest()
                finally:os.close(child)
            else:raise ValueError('links or special staged objects prohibited')
    walk(fd,'')
    if actual!=expected:raise ValueError('staged contents differ from exact approved manifest')
    if any(not any(n.startswith(d+'/') for n in expected) for d in directories):raise ValueError('unexpected empty staged directory')
    os.fsync(fd)

def publish(project,stage_name,expected):
    """固定目标、固定同级暂存命名；预检完整哈希后以内核 exclusive rename 发布。"""
    root=Path(project).absolute()
    if root.resolve(strict=True)!=root:raise ValueError('canonical project required')
    if not isinstance(stage_name,str) or not re.fullmatch(r'\.restyle-stage-[0-9a-f]{32}',stage_name):raise ValueError('invalid stage name')
    if not isinstance(expected,dict) or not expected or len(expected)>256:raise ValueError('invalid staged manifest')
    for name,value in expected.items():
        if not isinstance(name,str) or not name or name.startswith('/') or any(p in ('','..','.') for p in name.split('/')) or '\\' in name or not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):raise ValueError('invalid staged manifest member')
    boundary=ReadBoundary(root)
    for name in [PARENT,PARENT+'/'+stage_name,PARENT+'/'+DEST]:boundary.require(name)
    for name in expected:
        boundary.require(PARENT+'/'+stage_name+'/'+name);boundary.require(PARENT+'/'+DEST+'/'+name)
    parent=secure_fd(root,PARENT,directory=True)
    try:
        try:os.stat(DEST,dir_fd=parent,follow_symlinks=False)
        except FileNotFoundError:pass
        else:raise FileExistsError('destination already exists')
        stage=os.open(stage_name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        try:
            if os.fstat(stage).st_dev!=os.fstat(parent).st_dev:raise ValueError('stage must be on same filesystem')
            _verify(stage,expected,lambda relative: boundary.require(PARENT+'/'+stage_name+'/'+relative))
            current=secure_fd(root,PARENT,directory=True)
            try:
                if _identity(current)!=_identity(parent):raise ValueError('parent identity changed')
            finally:os.close(current)
            now=os.stat(stage_name,dir_fd=parent,follow_symlinks=False)
            if (now.st_dev,now.st_ino)!=_identity(stage):raise ValueError('stage identity changed')
            if ReadBoundary(root).sha256!=boundary.sha256:raise ValueError('policy changed')
            _rename_exclusive(parent,stage_name)
            os.fsync(parent)
            published=os.open(DEST,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
            try:
                if _identity(published)!=_identity(stage):raise ValueError('published identity differs')
            finally:os.close(published)
            return {'status':'published','destination':PARENT+'/'+DEST,'fileCount':len(expected)}
        finally:os.close(stage)
    finally:os.close(parent)
