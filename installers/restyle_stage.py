"""Exact approved 2.6.15 payload staging for the reviewed daily handoff.

No permission synthesis. The caller authenticates owner/platform approvals and
stops all writers independently. Interrupted partial stages remain blocked.
"""
import os
from pathlib import Path
import re
from contextlib import ExitStack
import fcntl
from daily_restyle_transition import inventory,sha,bytes_for,root_anchor,suite_digest
from read_boundary import ReadBoundary,secure_fd,bootstrap_bytes
from enable_harmonyos_reimplementation import INSTALL_LOCK,TARGET
from project_framework import LOCK_PATHS
from governance_journal import JOURNAL,LOCK
from staged_skill_publish import _verify
from transition_scope_preview import known_slots,FILES,DIRS

def payload(root,source,pin):
    b=ReadBoundary(root);sb=ReadBoundary(source)
    data=inventory(pin)
    data['selection.json']=bytes_for({'schemaVersion':'1.0','enabled':True,'projectRoot':str(root),'sourceRoot':str(source),'intent':'implementation-preserving-restyle','sourceReadOnly':True,'sourcePolicySha256':sb.sha256,'targetPolicySha256':b.sha256,'workflowPhase':'rebrand-production-before-acceptance','dailyHandoff':'explicit-after-acceptance'})
    if set(data)!=set(FILES):raise ValueError('not exact supported capability inventory')
    return data

def preview(root,source,pin,pin_sha,nonce,manifest_sha):
    root=Path(root).absolute();source=Path(source).absolute();pin=Path(pin).absolute()
    for p in (root,source,pin):
        if p.resolve(strict=True)!=p or not p.is_dir():raise ValueError('canonical real directories required')
    if any(a==b or a in b.parents or b in a.parents for a,b in ((root,source),(root,pin),(source,pin))):raise ValueError('separate project/source/trusted pin required')
    if suite_digest(pin)!=pin_sha or bootstrap_bytes(pin,'VERSION').decode().strip()!='2.6.15-standalone-restyle-rc1':raise ValueError('wrong independently verified 2.6.15 pin')
    b=ReadBoundary(root);sb=ReadBoundary(source)
    if sha(b.read_bytes('.ai-project/framework-installation.json'))!=manifest_sha:raise ValueError('manifest changed')
    stage,quarantine,active,slots=known_slots(nonce)
    for n in slots:b.require(n)
    # Absence is only absence after a successful permission and safe path check.
    for n in (stage,quarantine,active):
        try:fd=secure_fd(root,n,directory=True)
        except FileNotFoundError:pass
        else:os.close(fd);raise ValueError('already populated transition slot: '+n)
    for n in ('.agents','.agents/skills',JOURNAL):
        try:fd=secure_fd(root,n,directory=True)
        except FileNotFoundError:continue
        try:
            if n==JOURNAL and os.listdir(fd):raise ValueError('journal is not empty')
        finally:os.close(fd)
    for n in (*LOCK_PATHS,INSTALL_LOCK):
        b.require(n);fd=secure_fd(root,n);os.close(fd)
    try:fd=secure_fd(root,LOCK)
    except FileNotFoundError:pass
    else:os.close(fd)
    for n in ('.ai-project/identity.json','.ai-project/operation-holds.json','.ai-project/legacy-baseline.json','.ai-project/task-input.json'):
        if b.exists(n):raise ValueError('existing governance state: '+n)
    for n in ('.ai-project/tasks','.ai-project/upgrades','.ai-project/checkpoints','.ai-project/operation-holds'):
        try:fd=secure_fd(root,n,directory=True)
        except FileNotFoundError:continue
        try:
            if os.listdir(fd):raise ValueError('existing task or operation records')
        finally:os.close(fd)
    data=payload(root,source,pin)
    r={'schema':'restyle-stage-v1','root':str(root),'source':str(source),'pin':str(pin),'pinSha256':pin_sha,'nonce':nonce,'manifestSha256':manifest_sha,'rootAnchor':root_anchor(root),'sourceAnchor':root_anchor(source),'policySha256':b.sha256,'sourcePolicySha256':sb.sha256,'files':{n:sha(v) for n,v in data.items()},'writePaths':['.agents','.agents/skills',JOURNAL,LOCK,stage,*[stage+'/'+d for d in DIRS],*[stage+'/'+n for n in sorted(data)]],'authorityClaim':False}
    r['planSha256']=sha(bytes_for(r));return r

def stage(root,source,pin,pin_sha,nonce,manifest_sha,approved_sha,approval_reference,writer_evidence):
    if not isinstance(approval_reference,str) or not approval_reference.strip() or not isinstance(writer_evidence,str) or not writer_evidence.strip():raise ValueError('separate verified write approval and writer-stop evidence required')
    r=preview(root,source,pin,pin_sha,nonce,manifest_sha)
    if r['planSha256']!=approved_sha:raise ValueError('exact stage plan not approved')
    root=Path(r['root']);b=ReadBoundary(root)
    with ExitStack() as stack:
        locks=[]
        for name in (*LOCK_PATHS,INSTALL_LOCK):
            fd=secure_fd(root,name);stack.callback(os.close,fd);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append((name,fd))
        if preview(root,source,pin,pin_sha,nonce,manifest_sha)!=r:raise ValueError('stage preflight drift under locks')
        data=payload(root,Path(r['source']),Path(r['pin']))
        def check():
            if root_anchor(root)!=r['rootAnchor'] or ReadBoundary(root).sha256!=r['policySha256'] or root_anchor(Path(r['source']))!=r['sourceAnchor'] or ReadBoundary(Path(r['source'])).sha256!=r['sourcePolicySha256']:raise ValueError('anchor or policy drift')
            if suite_digest(Path(r['pin']))!=r['pinSha256'] or sha(b.read_bytes('.ai-project/framework-installation.json'))!=manifest_sha:raise ValueError('pin or manifest drift')
            for name,held in locks:
                current=secure_fd(root,name)
                try:
                    if (os.fstat(current).st_dev,os.fstat(current).st_ino)!=(os.fstat(held).st_dev,os.fstat(held).st_ino):raise ValueError('lock replaced')
                finally:os.close(current)
        def parent_fd(name):
            parent=str(Path(name).parent)
            return os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW) if parent=='.' else secure_fd(root,parent,directory=True)
        def check_parent(name,held):
            current=parent_fd(name)
            try:
                if (os.fstat(current).st_dev,os.fstat(current).st_ino)!=(os.fstat(held).st_dev,os.fstat(held).st_ino):raise ValueError('staging parent replaced')
            finally:os.close(current)
        def mkdir(name,existing=False):
            b.require(name);check();fd=parent_fd(name)
            try:
                check_parent(name,fd)
                try:os.mkdir(Path(name).name,0o700,dir_fd=fd)
                except FileExistsError:
                    if not existing:raise
                os.fsync(fd);check_parent(name,fd)
            finally:os.close(fd)
            d=secure_fd(root,name,directory=True);os.close(d)
        # No overwrite of payload or stage, exact parent creation is idempotent.
        for n in ('.agents','.agents/skills',JOURNAL):mkdir(n,True)
        check();b.require(LOCK)
        try:fd=secure_fd(root,LOCK)
        except FileNotFoundError:
            parent=secure_fd(root,'.ai-project',directory=True)
            try:
                check_parent(LOCK,parent)
                fd=os.open(Path(LOCK).name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent);os.fsync(fd);os.fsync(parent);check_parent(LOCK,parent)
            finally:os.close(parent)
        os.close(fd)
        location=known_slots(nonce)[0];mkdir(location)
        for n in DIRS:mkdir(location+'/'+n)
        for name,value in sorted(data.items()):
            path=location+'/'+name;b.require(path);check();parent=parent_fd(path)
            try:
                check_parent(path,parent)
                fd=os.open(Path(name).name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
                with os.fdopen(fd,'wb') as out:out.write(value);out.flush();os.fsync(out.fileno())
                os.fsync(parent);check_parent(path,parent)
            finally:os.close(parent)
        check();fd=secure_fd(root,location,directory=True)
        try:_verify(fd,r['files'],lambda n:b.require(location+'/'+n))
        finally:os.close(fd)
        return {'status':'staged','stage':location,'planSha256':approved_sha,'authorityClaim':False,'appChanged':False,'dailyTransitionApplied':False}
