"""底层现存文本文件事务原语，无 CLI；不授予治理交接权限或修改读取策略。"""
import base64
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'shared/task-routing'))
from read_boundary import ReadBoundary, secure_fd, canonical_relative
from task_runtime import root_anchor

LOCK='.ai-project/governance-transaction.lock'
JOURNAL='.ai-project/governance-journal'
ALLOWED={'.ai-project/lifecycle.json','.ai-project/framework-installation.json'}
MAX_IMAGE=65536
MAX_JOURNAL=400000
# 无阶段排序保证；不得据此启用 Skill 或部署治理转换。
def encoded(data):return base64.b64encode(data).decode()
def digest(data):return hashlib.sha256(data).hexdigest()
def bytes_for(record):return json.dumps(record,sort_keys=True,separators=(',',':')).encode()

@contextlib.contextmanager
def locked(root):
    b=ReadBoundary(root);b.require(LOCK);fd=secure_fd(root,LOCK)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        anchor=root_anchor(root)
        def guard():
            current=secure_fd(root,LOCK)
            try:
                a,c=os.fstat(fd),os.fstat(current)
                if (a.st_dev,a.st_ino)!=(c.st_dev,c.st_ino) or a.st_nlink!=1:raise ValueError('锁路径身份变化')
            finally:os.close(current)
            if root_anchor(root)!=anchor or ReadBoundary(root).sha256!=b.sha256:raise ValueError('根或策略变化')
        b.journal_guard=guard
        guard();yield b
    finally:os.close(fd)

def allowed_name(b,name):
    if canonical_relative(name)!=name or name not in ALLOWED:
        raise ValueError('不允许事务修改策略、任务或锁')
    b.require(name)

def journal_name(b,directory):
    if directory!=JOURNAL or canonical_relative(directory)!=directory:raise ValueError('仅接受固定事务目录')
    b.require(directory);b.require(directory+'/journal.json');b.journal_guard()

def read_bounded(root,b,name,limit):
    b.require(name);fd=secure_fd(root,name)
    try:
        if os.fstat(fd).st_size>limit:raise ValueError('控制内容超限')
        with os.fdopen(os.dup(fd),'rb') as stream:data=stream.read(limit+1)
        if len(data)>limit:raise ValueError('控制内容超限')
        return data
    finally:os.close(fd)

def check_ack(value):
    if not isinstance(value,str) or not value.strip() or len(value)>2048:raise ValueError('停写来源无效')

def replace_existing(root,b,name,expected,data):
    """持父目录描述符进行同目录原子替换；只恢复精确前/后像，不创建业务路径。"""
    b.journal_guard()
    allowed_name(b,name);parent=str(PurePosixPath(name).parent);leaf=PurePosixPath(name).name
    if parent=='.':raise ValueError('此原语暂不支持根级文件')
    b.require(parent);dfd=secure_fd(root,parent,directory=True)
    tmp='.governance-'+secrets.token_hex(12)
    try:
        if b.read_bytes(name)!=expected:raise ValueError('文件在写入前漂移')
        info=os.stat(leaf,dir_fd=dfd,follow_symlinks=False)
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,info.st_mode & 0o777,dir_fd=dfd)
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
            # 同一路径仍须指向当前描述符父目录；不追随替换的祖先。
            current=secure_fd(root,parent,directory=True)
            try:
                if (os.fstat(current).st_dev,os.fstat(current).st_ino)!=(os.fstat(dfd).st_dev,os.fstat(dfd).st_ino):raise ValueError('父目录变化')
            finally:os.close(current)
            if b.read_bytes(name)!=expected:raise ValueError('发布前漂移')
            b.journal_guard()
            os.replace(tmp,leaf,src_dir_fd=dfd,dst_dir_fd=dfd);os.fsync(dfd)
        finally:
            try:os.unlink(tmp,dir_fd=dfd)
            except FileNotFoundError:pass
    finally:os.close(dfd)

def publish_journal(root,b,directory,record):
    journal_name(b,directory);fd=secure_fd(root,directory,directory=True)
    data=bytes_for(record)
    if len(data)>MAX_JOURNAL:os.close(fd);raise ValueError('日志超限')
    tmp='.pending-'+secrets.token_hex(12)
    try:
        if os.listdir(fd):raise ValueError('事务目录含未知材料')
        out=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        with os.fdopen(out,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        b.journal_guard()
        # link 原子无覆盖发布；断电留下 pending 或多链接时恢复拒绝，尚未开始目标写入。
        os.link(tmp,'journal.json',src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
        os.fsync(fd);os.unlink(tmp,dir_fd=fd);os.fsync(fd)
    finally:os.close(fd)
    return digest(data)

def apply_exact(root,directory,changes,offline_ack,fault=None):
    """changes 是明确批准的 name→(前像,后像)；先持久化日志，再逐项发布。"""
    check_ack(offline_ack)
    if not isinstance(changes,dict) or not changes or not set(changes)<=ALLOWED:raise ValueError('精确范围无效')
    with locked(root) as b:
        journal_name(b,directory)
        items={}
        for name,(before,after) in changes.items():
            allowed_name(b,name)
            if name==directory or name.startswith(directory+'/') or directory.startswith(name+'/'):raise ValueError('事务和目标重叠')
            if not isinstance(before,bytes) or not isinstance(after,bytes) or len(before)>MAX_IMAGE or len(after)>MAX_IMAGE or read_bounded(root,b,name,MAX_IMAGE)!=before:raise ValueError('前像不匹配')
            items[name]={'before':encoded(before),'after':encoded(after)}
        record={'schema':'exact-existing-files-v1','anchor':root_anchor(root),'policySha256':b.sha256,'items':items,'offlineSource':offline_ack,'authorityClaim':False,'journalDirectory':directory}
        receipt=publish_journal(root,b,directory,record)
        if fault:fault('journal-durable',receipt)
        for name in sorted(changes):
            before,after=changes[name];replace_existing(root,b,name,before,after)
            if fault:fault(name,receipt)
        return receipt

def rollback_exact(root,directory,expected_journal_sha256,offline_ack,fault=None):
    check_ack(offline_ack)
    if not isinstance(expected_journal_sha256,str) or not re.fullmatch('[0-9a-f]{64}',expected_journal_sha256):raise ValueError('摘要无效')
    with locked(root) as b:
        journal_name(b,directory);fd=secure_fd(root,directory,directory=True)
        try:
            if sorted(os.listdir(fd))!=['journal.json']:raise ValueError('事务目录含未知材料')
        finally:os.close(fd)
        raw=read_bounded(root,b,directory+'/journal.json',MAX_JOURNAL)
        if digest(raw)!=expected_journal_sha256:raise ValueError('日志摘要漂移')
        r=json.loads(raw)
        if not isinstance(r,dict) or set(r)!={'schema','anchor','policySha256','items','offlineSource','authorityClaim','journalDirectory'} or r['authorityClaim'] is not False or r['journalDirectory']!=directory or r['schema']!='exact-existing-files-v1' or r['anchor']!=root_anchor(root) or r['policySha256']!=b.sha256:raise ValueError('日志归属或策略变化')
        check_ack(r['offlineSource'])
        if not isinstance(r['items'],dict) or not r['items'] or not set(r['items'])<=ALLOWED:raise ValueError('日志目标非法')
        decoded={}
        for name,item in r['items'].items():
            allowed_name(b,name)
            if not isinstance(item,dict) or set(item)!={'before','after'} or any(not isinstance(x,str) or len(x)>MAX_IMAGE*2 for x in item.values()):raise ValueError('日志前后像非法')
            before=base64.b64decode(item['before'],validate=True);after=base64.b64decode(item['after'],validate=True)
            if len(before)>MAX_IMAGE or len(after)>MAX_IMAGE:raise ValueError('前后像超限')
            current=read_bounded(root,b,name,MAX_IMAGE)
            if current not in (before,after):raise ValueError('恢复会覆盖后续修改')
            decoded[name]=(before,after)
        for name,(before,after) in sorted(decoded.items()):
            current=b.read_bytes(name)
            if current==before:continue
            replace_existing(root,b,name,after,before)
            if fault:fault(name,expected_journal_sha256)
        return {'status':'restored','journalSha256':expected_journal_sha256}
