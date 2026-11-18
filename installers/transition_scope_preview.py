"""Read-only compatibility preview for exact production-handoff scope.

This is not a policy registration API. The platform's authorization applies
independently; changes generated here require exact separate review.
"""
import hashlib
import json
import re
from pathlib import Path
from read_boundary import ReadBoundary, bootstrap_bytes, POLICY_PATH
from task_runtime import root_anchor
from exact_restyle_scope import _json

CAPABILITY = "harmonyos-app-reimplementation"
FILES = ("SKILL.md","selection.json","agents/openai.yaml",
         "references/behavior-contract.md","references/verification.md","references/visual-direction.md")
DIRS = ("agents","references")
PARENT = ".agents/skills"
METADATA = frozenset((".ai-project", ".ai-project/governance-journal",
 ".ai-project/governance-journal/journal.json", ".ai-project/governance-transaction.lock",
 ".ai-project/framework-installation.json",".ai-project/lifecycle.json",
 ".ai-project/framework-update.lock",".ai-project/tasks.lock",".ai-project/reimplementation-install.lock",
 ".ai-project/tasks",".ai-project/upgrades",".ai-project/checkpoints",
 ".ai-project/operation-holds",".ai-project/operation-holds.json",".ai-project/identity.json",
 ".ai-project/legacy-baseline.json",".ai-project/task-input.json","AGENTS.md"))
OLD_CONTROL_PARENTS = (".codex-rules",".codex-rules/harmonyos",".codex-rules/harmonyos/references",
                       ".codex-rules/_shared",".codex-rules/_shared/references",".codex-rules/_shared/schemas")

def known_slots(nonce):
    if not isinstance(nonce,str) or not re.fullmatch("[0-9a-f]{32}",nonce):
        raise ValueError("Require an explicit exact 32-character transaction nonce")
    stage=f"{PARENT}/.restyle-stage-{nonce}"
    quarantine=f"{PARENT}/.restyle-quarantine-{nonce}"
    active=f"{PARENT}/{CAPABILITY}"
    scope={".agents",PARENT,*METADATA,*OLD_CONTROL_PARENTS}
    for node in (stage,quarantine,active):
        scope.add(node)
        scope.update(f"{node}/{x}" for x in (*DIRS,*FILES))
    return stage,quarantine,active,scope

def preview(root, nonce, expected_before_sha):
    root=Path(root).absolute()
    if root.resolve(strict=True)!=root:
        raise ValueError("Real canonical project directory required")
    raw=bootstrap_bytes(root,POLICY_PATH)
    if len(raw)>100_000 or hashlib.sha256(raw).hexdigest()!=expected_before_sha:
        raise ValueError("Exact existing policy digest mismatch")
    old=_json(raw)
    current=ReadBoundary(root)
    if current.project_policy!=old or current.task_id is not None or old.get("mode")!="partial":
        raise ValueError("Only a partial non-task project scope can be reviewed")
    anchor=root_anchor(root)
    stage,quarantine,active,slots=known_slots(nonce)
    additions=sorted(x for x in slots if not current.allowed(x))
    new=dict(old,allow=[*old["allow"],*additions])
    if set(additions)&set(old["allow"]):
        raise ValueError("Existing explicit deny must remain effective")
    proposed=ReadBoundary(root,policy=new)
    if any(not proposed.allowed(x) for x in slots) or old.get("deny")!=new.get("deny"):
        raise ValueError("Existing denied slots cannot be silently relaxed")
    if root_anchor(root)!=anchor or bootstrap_bytes(root,POLICY_PATH)!=raw:
        raise ValueError("Directory or policy changed while reviewing")
    after=(json.dumps(new,ensure_ascii=False,indent=2)+"\n").encode()
    return {"status":"read-only-scope-preview","rootAnchor":anchor,
            "policyBeforeSha256":expected_before_sha,"policyAfterSha256":hashlib.sha256(after).hexdigest(),
            "nonce":nonce,"exactReadAdditions":additions,"stage":stage,"quarantine":quarantine,
            "activeSkill":active,"afterPolicyText":after.decode(),"writes":[],"authorityClaim":False}
