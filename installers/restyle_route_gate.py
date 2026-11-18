"""Pure validation of a prospective, parked-daily HarmonyOS rebrand receipt.

This module does not publish framework files, register permissions, or authorize
a transition. Legacy daily runtimes intentionally reject both protocol markers.
"""
from __future__ import annotations

import hashlib
import json
import re

MANIFEST = ".ai-project/framework-installation.json"
LIFECYCLE = ".ai-project/lifecycle.json"
ROUTING = "AGENTS.md"
SELECTION = ".agents/skills/harmonyos-app-reimplementation/selection.json"
SKILL = ".agents/skills/harmonyos-app-reimplementation/SKILL.md"
ROUTE_FILES = frozenset((LIFECYCLE, ROUTING, SELECTION, SKILL))
MARKERS = {
    "prepared": "harmony-restyle-transition-in-progress",
    "complete": "harmony-restyle-skill-only-v1",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_sha(value: object) -> None:
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise ValueError("Expected an independently verified SHA-256 value")


def _object(data: bytes) -> dict:
    def exact_members(pairs):
        contents = {}
        for key, value in pairs:
            if key in contents:
                raise ValueError("Duplicate receipt member")
            contents[key] = value
        return contents
    if not isinstance(data, bytes) or len(data) > 100_000:
        raise ValueError("Unexpected receipt data type or size")
    result = json.loads(data, object_pairs_hook=exact_members)
    if not isinstance(result, dict):
        raise ValueError("Receipt must be an object")
    return result


def marker(phase: str, transaction: str, plan_sha: str, original: bytes,
           route_files: dict[str, bytes]) -> bytes:
    """Describe only exact fixed route slots; does not write or decide authority."""
    if phase not in MARKERS or not isinstance(transaction, str) or not re.fullmatch("[0-9a-f]{32}", transaction):
        raise ValueError("Unknown handoff phase or transaction")
    _require_sha(plan_sha)
    old = _object(original)
    if (old.get("schemaVersion"), old.get("taskProtocolVersion"), old.get("framework"),
            old.get("lifecycle")) != ("1.0", "2", "harmonyos-daily-development-rules", "harmony-maintenance"):
        raise ValueError("Historical installation is not a HarmonyOS daily protocol-2 record")
    if set(route_files) != ROUTE_FILES or any(not isinstance(data, bytes) or not data for data in route_files.values()):
        raise ValueError("Route data must name every exact route file and no extras")
    result = {
        "schemaVersion": "1.0",
        "taskProtocolVersion": MARKERS[phase],
        "transactionId": transaction,
        "planSha256": plan_sha,
        "originalManifestSha256": _sha(original),
        "routeSha256": {name: _sha(data) for name, data in route_files.items()},
    }
    return (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def verify_final(current: bytes, expected_sha: str, transaction: str,
                 plan_sha: str, original: bytes, route_files: dict[str, bytes]) -> dict:
    """Fail closed on interrupted, drifted, partial or unconfirmed route publication."""
    _require_sha(expected_sha)
    expected = marker("complete", transaction, plan_sha, original, route_files)
    if current != expected or _sha(current) != expected_sha:
        raise ValueError("Handoff is incomplete, changed, or unrelated to the reviewed commit")
    selection = _object(route_files[SELECTION])
    if (
        selection.get("enabled") is not True or selection.get("sourceReadOnly") is not True
        or selection.get("workflowPhase") != "rebrand-production-before-acceptance"
        or selection.get("dailyHandoff") != "explicit-after-acceptance"
    ):
        raise ValueError("Invalid independent Skill selection")
    lifecycle = _object(route_files[LIFECYCLE])
    if (
        lifecycle.get("state") != "harmony-restyle-production"
        or lifecycle.get("defaultFramework") != "harmonyos-app-reimplementation"
        or lifecycle.get("dailyFramework") != "parked-until-acceptance"
    ):
        raise ValueError("Invalid rebrand-only project lifecycle")
    return {"status": "route-contents-verified", "executionAuthorized": False,
            "dailyParked": True, "claimsDeviceAcceptance": False}
