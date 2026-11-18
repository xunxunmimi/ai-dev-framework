"""Read-only preview of the sole owner-approved .agents parent check.

No authority is inferred from a caller-supplied source string. This does not
permit any file write, inspect .agents, or expand source/business read access.
"""
import hashlib
import json
import re
from pathlib import Path
from read_boundary import ReadBoundary, bootstrap_bytes, POLICY_PATH
from task_runtime import root_anchor


def _json(raw):
    def object_with_no_duplicate(members):
        result = {}
        for k, v in members:
            if k in result:
                raise ValueError("Duplicate policy member")
            result[k] = v
        return result
    return json.loads(raw, object_pairs_hook=object_with_no_duplicate)


def preview(root, expected_before_sha, owner_approval_reference):
    root = Path(root).absolute()
    if root.resolve(strict=True) != root:
        raise ValueError("Expected actual canonical project root")
    if not isinstance(expected_before_sha, str) or not re.fullmatch("[0-9a-f]{64}", expected_before_sha):
        raise ValueError("Independent policy byte digest is required")
    if not isinstance(owner_approval_reference, str) or not owner_approval_reference.strip():
        raise ValueError("Record the existing exact owner authorization")
    raw = bootstrap_bytes(root, POLICY_PATH)
    if len(raw) > 100_000 or hashlib.sha256(raw).hexdigest() != expected_before_sha:
        raise ValueError("Project policy does not match authorized before-image")
    anchor = root_anchor(root)
    old = _json(raw)
    if not isinstance(old, dict) or old.get("mode") != "partial" or old.get("scopeKind", "project") != "project":
        raise ValueError("Only a previously authorized partial project policy is eligible")
    active = ReadBoundary(root)
    if active.project_policy != old or active.task_id is not None:
        raise ValueError("Current effective policy is unrelated or task-bound")
    if active.allowed(".agents"):
        raise ValueError("Exact parent is already authorized; no amendment required")
    new = dict(old, allow=[*old["allow"], ".agents"])
    candidate = ReadBoundary(root, policy=new)
    if not candidate.allowed(".agents") or candidate.project_policy.get("deny") != old.get("deny"):
        raise ValueError("A deny cannot be modified or overridden by the exact parent amendment")
    if set(candidate.allow) - set(active.allow) != {".agents"} or set(active.allow) - set(candidate.allow):
        raise ValueError("Proposed permission delta is not the exact parent literal")
    if root_anchor(root) != anchor or bootstrap_bytes(root, POLICY_PATH) != raw:
        raise ValueError("Directory identity or project policy changed while reviewing")
    after = (json.dumps(new, ensure_ascii=False, indent=2) + "\n").encode()
    return {
        "status":"preview-only", "rootAnchor":anchor,
        "policyBeforeSha256":expected_before_sha, "policyAfterSha256":hashlib.sha256(after).hexdigest(),
        "newExactReadPaths":[".agents"], "recursiveReadAdded":False,
        "authorityClaim":False, "ownerApprovalReference":owner_approval_reference,
        "afterPolicyText":after.decode(), "writes":[],
    }
