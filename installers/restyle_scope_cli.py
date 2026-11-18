#!/usr/bin/env python3
"""Review and explicitly register an exact, owner-approved restyle scope."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"shared/task-routing"))
from transition_scope_preview import preview
from register_exact_parent import register

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True,type=Path)
    parser.add_argument('--nonce',required=True)
    parser.add_argument('--policy-before-sha256',required=True)
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('preview',help='Read only; show exact path additions and policy digests')
    apply=sub.add_parser('register',help='Register reviewed exact scope; owner/platform approvals must already exist')
    apply.add_argument('--policy-after-sha256',required=True)
    apply.add_argument('--installation-manifest-sha256',required=True)
    apply.add_argument('--owner-approval-reference',required=True)
    apply.add_argument('--writer-stop-evidence',required=True)
    apply.add_argument('--approved-exact-path',action='append',required=True)
    args=parser.parse_args()
    try:
        if args.action=='preview':
            result=preview(args.project,args.nonce,args.policy_before_sha256)
            result.pop('afterPolicyText')
            result.pop('rootAnchor')
        else:
            result=register(args.project,args.policy_before_sha256,args.policy_after_sha256,
                            args.installation_manifest_sha256,args.owner_approval_reference,
                            args.writer_stop_evidence,nonce=args.nonce,
                            approved_exact_delta=args.approved_exact_path)
    except (OSError,ValueError,TypeError) as error:
        print(json.dumps({'status':'blocked','reason':str(error)},ensure_ascii=False))
        return 2
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
