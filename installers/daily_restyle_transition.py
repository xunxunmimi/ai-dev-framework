"""仅新生成且尚无任务的日常安装到独立 Skill 的明确交接 API；不授予平台权限、不修改业务代码。"""
import base64
import hashlib
import json
import os
import stat
import re
from pathlib import Path
import secrets
from contextlib import contextmanager
from project_framework import installation_locks
from task_runtime import project_lock, root_anchor, suite_digest
from read_boundary import ReadBoundary, secure_fd, bootstrap_bytes
from enable_harmonyos_reimplementation import INSTALL_LOCK,TARGET
from plan_daily_to_restyle import plan as old_plan
from governance_journal import locked,publish_journal,JOURNAL,bytes_for,read_bounded
from staged_skill_publish import publish,_verify,PARENT,DEST
from restyle_route_gate import marker,verify_final,MANIFEST,LIFECYCLE,ROUTING,SELECTION,SKILL

LIMIT=400000
POINTER=b'# Project instructions\n\nRead `.ai-project/read-policy.json`, then `.agents/skills/harmonyos-app-reimplementation/SKILL.md` and its exact selection. Use only the rebrand Skill before explicit acceptance. The historical daily framework is parked; do not execute its task runtime. Daily handoff requires a separate explicit accepted transition.\n'
def sha(data):return hashlib.sha256(data).hexdigest()
def pack(data):return base64.b64encode(data).decode()
def unpack(value):return base64.b64decode(value,validate=True)

def stage_hashes(root,b,name):
    fd=secure_fd(root,PARENT+'/'+name,directory=True)
    try:
        # Expected set comes from the independently pinned new capability, never from directory traversal.
        return fd
    except Exception:os.close(fd);raise

def inventory(suite):
    base=suite/'capabilities/harmonyos-app-reimplementation';result={}
    for p in sorted(base.rglob('*')):
        if p.is_symlink():raise ValueError('untrusted capability link')
        if p.is_dir():continue
        name=p.relative_to(base).as_posix()
        if '__pycache__' in p.parts or p.name=='.DS_Store':continue
        result[name]=bootstrap_bytes(base,name)
    return result

def prepare(root,source,old_suite,old_sha,old_manifest_sha,new_suite,new_sha,stage_name,stop_evidence,historical_suite_path=None,historical_evidence=None):
    root=Path(root).absolute();source=Path(source).absolute();old_suite=Path(old_suite).absolute();new_suite=Path(new_suite).absolute()
    if root.resolve(strict=True)!=root:raise ValueError('canonical nonsymlink root required')
    if not isinstance(stop_evidence,str) or not stop_evidence.strip() or len(stop_evidence)>2048:raise ValueError('explicit independent writer evidence required')
    if source.resolve(strict=True)!=source or source==root or source in root.parents or root in source.parents:raise ValueError('separate canonical source required')
    if new_suite.resolve(strict=True)!=new_suite or root==new_suite or root in new_suite.parents or new_suite in root.parents:raise ValueError('separate trusted new pin required')
    if suite_digest(new_suite)!=new_sha or bootstrap_bytes(new_suite,'VERSION').decode().strip()!='2.6.15-standalone-restyle-rc1':raise ValueError('wrong new trusted pin')
    old=old_plan(root,old_suite,old_sha,old_manifest_sha,historical_suite_path,historical_evidence)
    b=ReadBoundary(root);sb=ReadBoundary(source)
    for name in [JOURNAL,JOURNAL+'/journal.json',ROUTING,MANIFEST,LIFECYCLE,TARGET,INSTALL_LOCK]:b.require(name)
    # Directory and explicit staging access must already be granted; never amend policy.
    directory=secure_fd(root,JOURNAL,directory=True)
    try:
        if os.listdir(directory):raise ValueError('transaction directory is not empty')
    finally:os.close(directory)
    from re import fullmatch
    if not isinstance(stage_name,str) or not fullmatch(r'\.restyle-stage-[0-9a-f]{32}',stage_name):raise ValueError('invalid stage')
    content=inventory(new_suite)
    selection={'schemaVersion':'1.0','enabled':True,'projectRoot':str(root),'sourceRoot':str(source),'intent':'implementation-preserving-restyle','sourceReadOnly':True,'sourcePolicySha256':sb.sha256,'targetPolicySha256':b.sha256,'workflowPhase':'rebrand-production-before-acceptance','dailyHandoff':'explicit-after-acceptance'}
    content['selection.json']=bytes_for(selection)
    expected={n:sha(v) for n,v in content.items()}
    fd=stage_hashes(root,b,stage_name)
    try:_verify(fd,expected,lambda n:b.require(PARENT+'/'+stage_name+'/'+n))
    finally:os.close(fd)
    for name in expected:b.require(TARGET+'/'+name)
    lifecycle=bytes_for({'state':'harmony-restyle-production','defaultFramework':'harmonyos-app-reimplementation','dailyFramework':'parked-until-acceptance'})
    before={n:pack(b.read_bytes(n)) for n in [MANIFEST,LIFECYCLE,ROUTING]}
    core={'schema':'reviewed-daily-restyle-v1','rootAnchor':root_anchor(root),'sourceAnchor':root_anchor(source),'policySha256':b.sha256,'sourcePolicySha256':sb.sha256,'oldArchiveSha256':old['archiveCandidates'],'oldPlanSha256':old['planSha256'],'newSuiteSha256':new_sha,'newSuite':str(new_suite),'stageName':stage_name,'skillSha256':expected,'before':before,'after':{LIFECYCLE:pack(lifecycle),ROUTING:pack(POINTER)},'sourceRoot':str(source),'stopEvidence':stop_evidence,'authorityClaim':False}
    core['ownerFactsSha256']=old['unmanagedFactsRequireReview']
    core['inputs']={'old_suite':str(old_suite),'old_sha':old_sha,'old_manifest_sha':old_manifest_sha,'historical_suite_path':historical_suite_path,'historical_evidence':historical_evidence}
    core_sha=sha(bytes_for(core));transaction=stage_name.removeprefix('.restyle-stage-')
    routes={LIFECYCLE:lifecycle,ROUTING:POINTER,SKILL:content['SKILL.md'],SELECTION:content['selection.json']}
    core['prepared']=pack(marker('prepared',transaction,core_sha,unpack(before[MANIFEST]),routes))
    core['complete']=pack(marker('complete',transaction,core_sha,unpack(before[MANIFEST]),routes))
    quarantine=PARENT+'/.restyle-quarantine-'+transaction
    b.require(PARENT)
    parentfd=secure_fd(root,PARENT,directory=True)
    os.close(parentfd)
    b.require(quarantine)
    for name in expected:b.require(quarantine+'/'+name)
    try:qfd=secure_fd(root,quarantine,directory=True)
    except FileNotFoundError:pass
    else:os.close(qfd);raise ValueError('quarantine already exists')
    core['transactionId']=transaction;core['coreSha256']=core_sha
    return core

@contextmanager
def guards(root):
    with installation_locks(root):
        with project_lock(root,INSTALL_LOCK):
            with locked(root) as b:yield b

def replace_control(root,b,name,before,after):
    if name not in (MANIFEST,LIFECYCLE,ROUTING):raise ValueError('unsupported control slot')
    b.require(name);b.journal_guard()
    parent=str(Path(name).parent);leaf=Path(name).name
    dfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW) if parent=='.' else secure_fd(root,parent,directory=True)
    temp='.restyle-control-'+secrets.token_hex(12)
    try:
        if b.read_bytes(name)!=before:raise ValueError('control preimage drift')
        mode=stat.S_IMODE(os.stat(leaf,dir_fd=dfd,follow_symlinks=False).st_mode)
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=dfd)
        os.fchmod(fd,mode)
        with os.fdopen(fd,'wb') as f:f.write(after);f.flush();os.fsync(f.fileno())
        current=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW) if parent=='.' else secure_fd(root,parent,directory=True)
        try:
            if (os.fstat(current).st_dev,os.fstat(current).st_ino)!=(os.fstat(dfd).st_dev,os.fstat(dfd).st_ino):raise ValueError('control parent changed')
        finally:os.close(current)
        b.journal_guard()
        if b.read_bytes(name)!=before:raise ValueError('control changed before publication')
        os.replace(temp,leaf,src_dir_fd=dfd,dst_dir_fd=dfd);os.fsync(dfd)
    finally:
        try:os.unlink(temp,dir_fd=dfd)
        except FileNotFoundError:pass
        os.close(dfd)

def decode_record(data):
    if not isinstance(data,bytes) or len(data)>LIMIT:raise ValueError('bounded record bytes required')
    def unique(pairs):
        out={}
        for k,v in pairs:
            if k in out:raise ValueError('duplicate record key')
            out[k]=v
        return out
    return json.loads(data.decode('utf-8'),object_pairs_hook=unique)

def record_shape(r):
    keys={'schema','rootAnchor','sourceAnchor','policySha256','sourcePolicySha256','oldArchiveSha256','oldPlanSha256','newSuiteSha256','newSuite','stageName','skillSha256','before','after','sourceRoot','stopEvidence','authorityClaim','ownerFactsSha256','inputs','prepared','complete','transactionId','coreSha256'}
    if not isinstance(r,dict) or set(r)!=keys or len(bytes_for(r))>LIMIT:raise ValueError('record shape')
    for key in ('rootAnchor','sourceAnchor'):
        a=r[key]
        if not isinstance(a,dict) or set(a)!={'projectRoot','device','inode'} or not isinstance(a['projectRoot'],str) or type(a['device']) is not int or type(a['inode']) is not int:raise ValueError('anchor shape')
    for key in ('policySha256','sourcePolicySha256','oldPlanSha256','newSuiteSha256','coreSha256'):
        if not isinstance(r[key],str) or not re.fullmatch('[0-9a-f]{64}',r[key]):raise ValueError('digest shape')
    for key in ('oldArchiveSha256','skillSha256','ownerFactsSha256'):
        m=r[key]
        if not isinstance(m,dict) or not m or len(m)>256:raise ValueError('inventory shape')
        for n,h in m.items():
            if not isinstance(n,str) or n.startswith('/') or '\\' in n or any(x in ('','.','..') for x in n.split('/')) or not isinstance(h,str) or not re.fullmatch('[0-9a-f]{64}',h):raise ValueError('inventory member')
    for key in ('before','after'):
        if not isinstance(r[key],dict):raise ValueError('images shape')
        for n,v in r[key].items():
            if not isinstance(v,str) or len(unpack(v))>65536:raise ValueError('image bound')
    for key in ('prepared','complete'):
        if not isinstance(r[key],str) or len(unpack(r[key]))>100000:raise ValueError('marker bound')
    for key in ('newSuite','sourceRoot','stopEvidence','stageName','transactionId'):
        if not isinstance(r[key],str) or not r[key] or len(r[key])>2048:raise ValueError('string shape')
    if not re.fullmatch(r'\.restyle-stage-[0-9a-f]{32}',r['stageName']) or not re.fullmatch('[0-9a-f]{32}',r['transactionId']):raise ValueError('fixed stage identity')
    i=r['inputs']
    if not isinstance(i,dict) or set(i)!={'old_suite','old_sha','old_manifest_sha','historical_suite_path','historical_evidence'}:raise ValueError('input shape')
    for key in ('old_suite','old_sha','old_manifest_sha'):
        if not isinstance(i[key],str) or not i[key]:raise ValueError('input type')
    for key in ('historical_suite_path','historical_evidence'):
        if i[key] is not None and (not isinstance(i[key],str) or not i[key] or len(i[key])>4096):raise ValueError('historical input type')

def validate_record(root,record,approved_sha):
    if Path(root).absolute().resolve(strict=True)!=Path(root).absolute():raise ValueError("root alias")
    record_shape(record)
    if sha(bytes_for(record))!=approved_sha or record.get('schema')!='reviewed-daily-restyle-v1' or record.get('authorityClaim') is not False or record['rootAnchor']!=root_anchor(root):raise ValueError('exact approved record mismatch')
    if set(record['before'])!={MANIFEST,LIFECYCLE,ROUTING} or set(record['after'])!={LIFECYCLE,ROUTING} or unpack(record['after'][ROUTING])!=POINTER:raise ValueError('invalid exact slots')
    base={k:v for k,v in record.items() if k not in ('prepared','complete','transactionId','coreSha256')}
    if sha(bytes_for(base))!=record['coreSha256'] or record['transactionId']!=record['stageName'][len('.restyle-stage-'):]:raise ValueError('core binding changed')

def write_scope(record):
    # Separate from read-policy. No app code, source, policy, pin or arbitrary paths.
    q=PARENT+'/.restyle-quarantine-'+record['transactionId']
    return sorted([MANIFEST,LIFECYCLE,ROUTING,JOURNAL,PARENT+'/'+record['stageName'],TARGET,q])

def require_write_approval(root,record,approved_sha,approval):
    # Trusted caller must authenticate this separate external receipt; this API never issues one.
    fields={'planSha256','targetRoot','scope','evidence','appWritesAuthorized'}
    if not isinstance(approval,dict) or set(approval)!=fields or approval['planSha256']!=approved_sha or approval['targetRoot']!=str(Path(root).absolute()) or approval['scope']!=write_scope(record) or approval['appWritesAuthorized'] is not False or not isinstance(approval['evidence'],str) or not approval['evidence'].strip():raise ValueError('separate exact governance write approval required; read grant is insufficient')

def apply(root,record,approved_sha,fault=None,*,write_approval=None):
    """从明确固定输入重新预检，不接受调用方提供的可信回调。"""
    root=Path(root);validate_record(root,record,approved_sha)
    require_write_approval(root,record,approved_sha,write_approval)
    i=record['inputs']
    fresh=prepare(root,record['sourceRoot'],i['old_suite'],i['old_sha'],i['old_manifest_sha'],record['newSuite'],record['newSuiteSha256'],record['stageName'],record['stopEvidence'],i['historical_suite_path'],i['historical_evidence'])
    if fresh!=record:raise ValueError('preflight changed')
    with guards(root) as b:
        if b.sha256!=record['policySha256']:raise ValueError('policy changed under locks')
        for name,value in record['oldArchiveSha256'].items():
            if sha(b.read_bytes(name))!=value:raise ValueError('old control changed under locks')
        from plan_daily_to_restyle import directory_empty
        for name in ['.ai-project/tasks','.ai-project/upgrades','.ai-project/operation-holds','.ai-project/checkpoints']:
            directory_empty(root,b,name)
        for name in ['.ai-project/identity.json','.ai-project/operation-holds.json','.ai-project/legacy-baseline.json','.ai-project/task-input.json']:
            if b.exists(name):raise ValueError('new state appeared')
        check_invariants(root,b,record)
        receipt=publish_journal(root,b,JOURNAL,record)
        if receipt!=approved_sha:raise ValueError('journal mismatch')
        def point(name):
            if fault:fault(name)
        point('journal')
        replace_control(root,b,MANIFEST,unpack(record['before'][MANIFEST]),unpack(record['prepared']));point('park')
        for name in (LIFECYCLE,ROUTING):replace_control(root,b,name,unpack(record['before'][name]),unpack(record['after'][name]));point(name)
        publish(root,record['stageName'],record['skillSha256']);point('skill')
        verify_tree(root,b,TARGET,record['skillSha256'])
        check_invariants(root,b,record)
        routes={n:b.read_bytes(n) for n in (LIFECYCLE,ROUTING,SKILL,SELECTION)}
        verify_final(unpack(record['complete']),sha(unpack(record['complete'])),record['transactionId'],record['coreSha256'],unpack(record['before'][MANIFEST]),routes)
        replace_control(root,b,MANIFEST,unpack(record['prepared']),unpack(record['complete']));point('complete')
        return {'status':'complete','executionAuthorized':False,'journalSha256':receipt}

def verify_tree(root,b,path,expected):
    b.require(path)
    fd=secure_fd(root,path,directory=True)
    try:_verify(fd,expected,lambda n:b.require(path+'/'+n))
    finally:os.close(fd)

def check_invariants(root,b,r):
    if root_anchor(root)!=r['rootAnchor'] or ReadBoundary(root).sha256!=r['policySha256']:raise ValueError('target drift')
    source=Path(r['sourceRoot'])
    if source.resolve(strict=True)!=source or root_anchor(source)!=r['sourceAnchor'] or ReadBoundary(source).sha256!=r['sourcePolicySha256']:raise ValueError('source drift')
    pin=Path(r['newSuite'])
    if pin.resolve(strict=True)!=pin or suite_digest(pin)!=r['newSuiteSha256']:raise ValueError('new pin drift')
    for name,expected in r['ownerFactsSha256'].items():
        if sha(b.read_bytes(name))!=expected:raise ValueError('owner fact drift')
    b.journal_guard()

def quarantine_skill(root,b,r):
    # Atomically retain exact active bytes, never recursively delete active governance.
    dest='.restyle-quarantine-'+r['transactionId'];path=PARENT+'/'+dest
    b.require(path);b.require(TARGET)
    parent=secure_fd(root,PARENT,directory=True)
    try:
        def present(name):
            try:os.stat(name,dir_fd=parent,follow_symlinks=False);return True
            except FileNotFoundError:return False
        active=present(DEST);saved=present(dest)
        if active and saved:raise ValueError('active and quarantine both exist')
        if saved:verify_tree(root,b,path,r['skillSha256']);return
        if not active:return
        verify_tree(root,b,TARGET,r['skillSha256'])
        for n in r['skillSha256']:b.require(path+'/'+n)
        before=os.stat(DEST,dir_fd=parent,follow_symlinks=False)
        b.journal_guard()
        current=secure_fd(root,PARENT,directory=True)
        try:
            if (os.fstat(current).st_dev,os.fstat(current).st_ino)!=(os.fstat(parent).st_dev,os.fstat(parent).st_ino):raise ValueError('parent moved')
        finally:os.close(current)
        # Kernel no-replace rename on the same anchored parent descriptor.
        import ctypes,sys
        lib=ctypes.CDLL(None,use_errno=True)
        if sys.platform=='linux' and hasattr(lib,'renameat2'):fn=lib.renameat2;flag=1
        elif sys.platform=='darwin' and hasattr(lib,'renameatx_np'):fn=lib.renameatx_np;flag=4
        else:raise RuntimeError('exclusive rename unavailable')
        fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];fn.restype=ctypes.c_int
        if fn(parent,os.fsencode(DEST),parent,os.fsencode(dest),flag):raise OSError(ctypes.get_errno(),'quarantine rename failed')
        os.fsync(parent)
        after=os.stat(dest,dir_fd=parent,follow_symlinks=False)
        if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):raise ValueError('quarantine identity changed')
        verify_tree(root,b,path,r['skillSha256'])
    finally:os.close(parent)

def rollback(root,record,approved_sha,fault=None,*,write_approval=None):
    root=Path(root);validate_record(root,record,approved_sha)
    require_write_approval(root,record,approved_sha,write_approval)
    with guards(root) as b:
        raw=read_bounded(root,b,JOURNAL+'/journal.json',LIMIT)
        if decode_record(raw)!=record or raw!=bytes_for(record) or b.sha256!=record['policySha256']:raise ValueError('journal or policy drift')
        fd=secure_fd(root,JOURNAL,directory=True)
        try:
            if sorted(os.listdir(fd))!=['journal.json']:raise ValueError('unknown journal material')
        finally:os.close(fd)
        current=b.read_bytes(MANIFEST);original=unpack(record['before'][MANIFEST])
        if current not in (original,unpack(record['prepared']),unpack(record['complete'])):raise ValueError('manifest drift')
        for n in (LIFECYCLE,ROUTING):
            if b.read_bytes(n) not in (unpack(record['before'][n]),unpack(record['after'][n])):raise ValueError('route drift')
        # Validate all Skill bytes before restoring anything.
        try:fd=secure_fd(root,TARGET,directory=True)
        except FileNotFoundError:pass
        else:
            try:_verify(fd,record['skillSha256'],lambda n:b.require(TARGET+'/'+n))
            finally:os.close(fd)
        q=PARENT+'/.restyle-quarantine-'+record['transactionId']
        try:verify_tree(root,b,q,record['skillSha256'])
        except FileNotFoundError:pass
        if current==unpack(record['complete']):replace_control(root,b,MANIFEST,current,unpack(record['prepared']))
        quarantine_skill(root,b,record)
        if fault:fault('quarantine')
        for n in (ROUTING,LIFECYCLE):
            now=b.read_bytes(n)
            if now!=unpack(record['before'][n]):replace_control(root,b,n,now,unpack(record['before'][n]))
        now=b.read_bytes(MANIFEST)
        if now!=original:replace_control(root,b,MANIFEST,now,original)
        return {'status':'restored','oldManifestRestoredLast':True}
