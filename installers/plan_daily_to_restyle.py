#!/usr/bin/env python3
"""只读交接计划：仅首次生成且未开任务的日常框架；没有 apply 或卸载入口。"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from enable_harmonyos_reimplementation import TARGET, INSTALL_LOCK
from project_framework import LOCK_PATHS, MANAGED_MAPPINGS
from read_boundary import ReadBoundary, secure_fd, bootstrap_bytes
from task_runtime import root_anchor, suite_digest, digest

@contextlib.contextmanager
def existing_locks(root, boundary):
    """只锁已有普通文件，不创建锁；失败关闭全部已取得描述符。"""
    fds=[]
    try:
        for name in (*LOCK_PATHS, INSTALL_LOCK):
            boundary.require(name)
            try: fd=secure_fd(root,name)
            except FileNotFoundError:
                if name==INSTALL_LOCK: continue
                raise ValueError('缺少现有维护锁: '+name)
            fds.append(fd); fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:
        for fd in reversed(fds): os.close(fd)

def directory_empty(root,boundary,name):
    boundary.require(name)
    try: fd=secure_fd(root,name,directory=True)
    except FileNotFoundError:return
    try:
        if os.listdir(fd):raise ValueError('已有记录或未知材料: '+name)
    finally:os.close(fd)

def entry_snapshot(root, boundary):
    """只枚举明确授权的控制目录；前后复核名称和文件系统身份。"""
    result={}
    for name in ['.ai-project','.ai-project/tasks','.ai-project/upgrades','.ai-project/operation-holds',
                 '.ai-project/checkpoints','.codex-rules','.codex-rules/harmonyos','.codex-rules/harmonyos/references',
                 '.codex-rules/_shared','.codex-rules/_shared/references','.codex-rules/_shared/schemas','.agents','.agents/skills']:
        boundary.require(name)
        try: fd=secure_fd(root,name,directory=True)
        except FileNotFoundError:
            result[name]=None;continue
        try:
            result[name]=[(entry, tuple(os.stat(entry,dir_fd=fd,follow_symlinks=False)[i] for i in (0,1,2,3,6,8,9))) for entry in sorted(os.listdir(fd))]
        finally:os.close(fd)
    return result

def plan(project, trusted_suite, expected_suite_sha256, expected_manifest_sha256, historical_suite_path=None, historical_evidence=None):
    root=project.absolute()
    if root.resolve(strict=True)!=root: raise ValueError('要求真实规范目录')
    boundary=ReadBoundary(root);anchor=root_anchor(root)
    # trusted_suite 和两个摘要必须来自独立已核 pin/安装来源，不从项目 manifest 自授信。
    suite=trusted_suite.absolute()
    if suite.resolve(strict=True)!=suite or suite==root or root in suite.parents or suite in root.parents:
        raise ValueError('可信固定套件须与项目分离且无链接')
    if any(not re.fullmatch('[0-9a-f]{64}', value) for value in (expected_suite_sha256,expected_manifest_sha256)):
        raise ValueError('缺少独立核实的摘要')
    raw_manifest=boundary.read_bytes('.ai-project/framework-installation.json')
    if hashlib.sha256(raw_manifest).hexdigest()!=expected_manifest_sha256:
        raise ValueError('安装清单精确摘要变化')
    manifest=json.loads(raw_manifest)
    historical = historical_suite_path is not None
    if historical:
        # 仅比较原生回执给出的精确字符串；绝不 resolve/stat/read 项目内旧 pin。
        version=manifest.get('suiteVersion')
        if not isinstance(version,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*',version):
            raise ValueError('历史版本标识不安全')
        expected_path=str(root/'.framework-pins'/(version+'-'+expected_suite_sha256[:16]))
        if (not isinstance(historical_suite_path,str) or historical_suite_path!=expected_path or
            manifest.get('suiteRoot')!=historical_suite_path or not isinstance(historical_evidence,str) or not historical_evidence.strip()):
            raise ValueError('历史 pin 路径或独立回执不匹配')
    elif manifest.get('suiteRoot')!=str(suite):
        raise ValueError('suiteRoot 与独立可信固定目录不符')
    fields={'schemaVersion','suiteVersion','suiteSha256','framework','lifecycle','installedAt','suiteRoot','managedFiles','contentSha256','taskProtocolVersion'}
    if set(manifest)!=fields or manifest['schemaVersion']!='1.0' or manifest['taskProtocolVersion']!='2':
        raise ValueError('未知安装清单字段或协议')
    mappings=dict(MANAGED_MAPPINGS['harmonyos-daily-development-rules'])
    entries=manifest['managedFiles']
    if not isinstance(entries,list) or len(entries)!=len(mappings) or any(not isinstance(x,dict) or set(x)!={'path','source','installedSha256','sourceSha256'} for x in entries):
        raise ValueError('受管映射字段或数量不符')
    if {x['path']:x['source'] for x in entries}!=mappings:
        raise ValueError('受管映射不是精确已知集合')
    if manifest['suiteSha256']!=expected_suite_sha256 or suite_digest(suite)!=expected_suite_sha256:
        raise ValueError('可信固定套件摘要变化')
    if bootstrap_bytes(suite,'VERSION').decode().strip()!=manifest['suiteVersion']:
        raise ValueError('套件版本不符')
    for item in entries:
        expected=hashlib.sha256(bootstrap_bytes(suite,item['source'])).hexdigest()
        if item['sourceSha256']!=expected or item['installedSha256']!=expected or hashlib.sha256(boundary.read_bytes(item['path'])).hexdigest()!=expected:
            raise ValueError('受管源或安装精确摘要漂移')
    if hashlib.sha256(json.dumps({x['source']:x['sourceSha256'] for x in entries},sort_keys=True).encode()).hexdigest()!=manifest['contentSha256']:
        raise ValueError('清单内容摘要不符')
    with existing_locks(root,boundary):
        directory_entries=entry_snapshot(root,boundary)
        for name in ['.ai-project/tasks','.ai-project/upgrades','.ai-project/operation-holds','.ai-project/checkpoints']:
            directory_empty(root,boundary,name)
        for name in ['.ai-project/identity.json','.ai-project/operation-holds.json',
                     '.ai-project/legacy-baseline.json','.ai-project/task-input.json',TARGET]:
            boundary.require(name)
            # Skill 目录存在即阻断；类型不符也不能当缺失。
            try: fd=secure_fd(root,name,directory=name==TARGET)
            except FileNotFoundError:continue
            os.close(fd);raise ValueError('已有身份或 Skill: '+name)
        rules={'version':manifest['suiteVersion'],'managedSha256':digest(entries),'trustedSuiteSha256':expected_suite_sha256}
        if manifest.get('framework')!='harmonyos-daily-development-rules' or manifest.get('lifecycle')!='harmony-maintenance':
            raise ValueError('不是初始日常安装')
        setup=bootstrap_bytes(suite,'frameworks/harmonyos-daily-development-rules/setup-project.sh').decode()
        pointer=re.search(r"^agents_pointer='([^']+)'$",setup,re.M)
        if not pointer:raise ValueError('未知 setup 模板')
        agents='# Project instructions\n\n<!-- harmonyos-daily-development-rules -->\n'+pointer.group(1)+'\n'
        if boundary.read_text('AGENTS.md')!=agents:raise ValueError('AGENTS 不是未修改生成内容')
        expected={'schemaVersion':'1.0','frameworkVersion':manifest['suiteVersion'],'state':'harmony-maintenance','defaultFramework':'harmonyos-daily-development-rules','migrationFramework':'archived-read-only','routing':'one-primary-framework-per-task'}
        if boundary.read_json('.ai-project/lifecycle.json')!=expected:raise ValueError('lifecycle 漂移')
        names=[x['path'] for x in manifest['managedFiles']]+['AGENTS.md','.ai-project/lifecycle.json','.ai-project/framework-installation.json']
        hashes={name:hashlib.sha256(boundary.read_bytes(name)).hexdigest() for name in sorted(set(names))}
        # 锁前校验不能替代归档候选的锁内校验；同名文件在取锁前可被编辑。
        for item in entries:
            if hashes[item['path']] != item['installedSha256']:
                raise ValueError('取锁后受管安装摘要漂移')
        # 非受管事实文件不宣称纯生成、不提出自动移动；列出需另行审阅。
        facts=['.codex-rules/harmonyos/PROJECT.md','.ai-project/detected-capabilities.json','.ai-project/evidence-index.md']
        fact_hashes={name:hashlib.sha256(boundary.read_bytes(name)).hexdigest() for name in facts}
        if root_anchor(root)!=anchor or ReadBoundary(root).sha256!=boundary.sha256:raise ValueError('根或策略漂移')
        if entry_snapshot(root,boundary)!=directory_entries:raise ValueError('控制目录条目漂移')
        if boundary.read_bytes('.ai-project/framework-installation.json')!=raw_manifest:raise ValueError('清单期间漂移')
        for name, value in {**hashes,**fact_hashes}.items():
            if hashlib.sha256(boundary.read_bytes(name)).hexdigest()!=value:raise ValueError('控制文件期间漂移')
        if suite_digest(suite)!=expected_suite_sha256:raise ValueError('固定源期间漂移')
        result={'status':'review-required','applySupported':False,'projectAnchor':anchor,'policySha256':boundary.sha256,
          'rulesIdentity':rules,'archiveCandidates':hashes,'unmanagedFactsRequireReview':fact_hashes,
          'existingLocksAcquired':True,'allWritersStoppedVerified':False,
          'requiredApproval':'逐路径归档前像、保留原 manifest 的正式转换记录、AGENTS/lifecycle 改动和新事务目录；策略差异须另列，不能假定已获授权。',
          'writes':[]}
        # 明确列出未来工具动作，不提供 apply。所有前像和审批必须逐项匹配；计划不授予权限。
        result['sourceVerification']={'mode':'historical-mapping-to-independent-copy' if historical else 'direct-trusted-copy',
            'historicalInstalledSourceReverified':not historical,
            'historicalSuitePath':historical_suite_path,
            'historicalEvidenceSha256':digest(historical_evidence) if historical else None,
            'independentSourceSha256':expected_suite_sha256,
            'limitation':'历史路径及清单由独立回执绑定；项目内旧 pin 字节未读取、未重新验证。仅用于只读审阅，不能据此 apply。' if historical else None}
        result['proposedActions'] = [
            {'step': 1, 'action': 'verify-independent-authorization',
             'requires': ['canonical project anchor', 'trusted pin provenance', 'exact plan digest',
                          'explicit governance transition approval', 'independent all-writer quiescence evidence'],
             'mustNotInfer': ['lock availability implies no writers', 'native receipt grants archive writes']},
            {'step': 2, 'action': 'review-owner-facts-and-policy-delta',
             'preimages': fact_hashes,
             'policyPreimageSha256': boundary.sha256,
             'constraint': '缺少 .agents 授权只提出精确新增项；源策略不变。任何新事务目录权限单独列明，禁止用 ** 替代。'},
            {'step': 3, 'action': 'prepare-exact-preimage-archive',
             'preimages': hashes, 'destination': 'requires-explicitly-approved-transaction-directory',
             'constraint': '只复制已批准控制前像，逐文件核验；不移动或删除任务，不改原摘要。'},
            {'step': 4, 'action': 'publish-explicit-deactivation-and-skill-handoff',
             'requires': ['reviewed new governance bytes', 'old runtime fail-closed guard',
                          'supported transition receipt validator', 'atomic recovery journal protocol'],
             'constraint': '保留原 manifest 作为历史安装证据；不能删除它伪装首次安装。独立 Skill 不读取旧日常路由。'},
            {'step': 5, 'action': 'verify-or-explicit-rollback',
             'requires': ['all writers remain stopped', 'exact expected postimage hashes', 'no intervening task or artifact creation'],
             'constraint': '只有当前字节仍匹配该事务后像才恢复前像；未知或并发变化保持阻断，不覆盖。'}]
        result['applyBlockers'] = ['独立写者停写证明尚未核实',
            '归档及控制状态转换的精确权限未由本计划授予',
            '旧运行器停用与新选择记录共同提交的事务/恢复协议尚未实现',
            '未登记 .agents 授权不能通过此工具绕过']
        result['planSha256']=digest(result);return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',required=True,type=Path);p.add_argument('--trusted-suite',required=True,type=Path);p.add_argument('--suite-sha256',required=True);p.add_argument('--manifest-sha256',required=True);p.add_argument('--historical-suite-path');p.add_argument('--historical-evidence');a=p.parse_args()
    try: print(json.dumps(plan(a.project,a.trusted_suite,a.suite_sha256,a.manifest_sha256,a.historical_suite_path,a.historical_evidence),ensure_ascii=False,indent=2))
    except (ValueError,OSError,TypeError,KeyError) as e:
        print(json.dumps({'status':'blocked','reason':str(e)},ensure_ascii=False));return 2
    return 0
if __name__=='__main__':raise SystemExit(main())
