#!/usr/bin/env python3
"""Explicit reviewed daily-to-Skill handoff. Does not grant platform permissions.

Scope registration has its separate restyle_scope_cli.py preview/register entry.
Read-only plan emits exact record to stdout; keep it outside the project.
"""
import argparse,json,sys
from pathlib import Path
from restyle_stage import preview as stage_preview,stage
import daily_restyle_transition as transition
from governance_journal import bytes_for

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',required=True,type=Path)
    sub=p.add_subparsers(dest='action',required=True)
    for action in ('stage-preview','stage','plan'):
        a=sub.add_parser(action);a.add_argument('--source',required=True,type=Path);a.add_argument('--new-pin',required=True,type=Path);a.add_argument('--new-pin-sha256',required=True);a.add_argument('--nonce',required=True);a.add_argument('--manifest-sha256',required=True)
        if action=='stage':
            a.add_argument('--approved-plan-sha256',required=True);a.add_argument('--owner-write-approval-reference',required=True);a.add_argument('--writer-stop-evidence',required=True)
        if action=='plan':
            a.add_argument('--old-pin',required=True,type=Path);a.add_argument('--old-pin-sha256',required=True);a.add_argument('--historical-pin-path');a.add_argument('--historical-pin-evidence');a.add_argument('--writer-stop-evidence',required=True)
    for action in ('apply','rollback'):
        a=sub.add_parser(action);a.add_argument('--record',required=True,type=Path);a.add_argument('--approved-plan-sha256',required=True);a.add_argument('--owner-write-approval-reference',required=True)
        a.add_argument('--writer-stop-evidence',required=True,help='Current independently verified quiescence, not merely an old lock observation')
        a.add_argument('--approved-write-path',required=True,action='append')
    a=p.parse_args()
    try:
        if a.action in ('stage-preview','stage'):
            args=(a.project,a.source,a.new_pin,a.new_pin_sha256,a.nonce,a.manifest_sha256)
            r=stage_preview(*args) if a.action=='stage-preview' else stage(*args,a.approved_plan_sha256,a.owner_write_approval_reference,a.writer_stop_evidence)
        elif a.action=='plan':
            r=transition.prepare(a.project,a.source,a.old_pin,a.old_pin_sha256,a.manifest_sha256,a.new_pin,a.new_pin_sha256,'.restyle-stage-'+a.nonce,a.writer_stop_evidence,a.historical_pin_path,a.historical_pin_evidence)
            r={'status':'read-only-transition-plan','record':r,'planSha256':transition.sha(bytes_for(r)),'requiredWritePaths':transition.write_scope(r),'authorityClaim':False}
        else:
            if not a.writer_stop_evidence.strip():raise ValueError('current independent writer-stop evidence required')
            from read_boundary import bootstrap_bytes
            path=a.record.absolute()
            if path.resolve(strict=True)!=path or a.project.absolute()==path or a.project.absolute() in path.parents:raise ValueError('reviewed record must be canonical and outside project')
            record=transition.decode_record(bootstrap_bytes(path.parent,path.name))
            approval={'planSha256':a.approved_plan_sha256,'targetRoot':str(a.project.absolute()),'scope':sorted(a.approved_write_path),'evidence':a.owner_write_approval_reference,'appWritesAuthorized':False}
            r=getattr(transition,a.action)(a.project,record,a.approved_plan_sha256,write_approval=approval)
        print(json.dumps(r,ensure_ascii=False,indent=2));return 0
    except (OSError,ValueError,TypeError,KeyError) as error:
        print(json.dumps({'status':'blocked','reason':str(error),'authorityClaim':False},ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
