#!/usr/bin/env python3
"""Release evidence contract; does not sign, install, pay, or publish."""
from __future__ import annotations
import hashlib


def validate_release(config: dict, selected: list[str], results: list[dict], boundary, binding: dict) -> list[str]:
    """核验 Release 命令与最终包/人工记录关联；不读取私钥或签名配置。"""
    errors = []
    gates = {x['id']: x for x in config.get('gates', [])}
    release_commands = [gates[x] for x in selected if x in gates and gates[x].get('kind', 'command') == 'command' and gates[x].get('buildMode') == 'release' and 'buildMode=release' in gates[x].get('argv', [])]
    passed_ids = {x['id'] for x in results if x.get('status') == 'passed'}
    if not release_commands or not any(x['id'] in passed_ids for x in release_commands):
        errors.append('confirmed successful Release build missing (Debug cannot substitute)')
    manual = gates.get('release-manual-evidence', {})
    try:
        name = manual.get('evidence', '')
        from pathlib import Path
        path = Path(name)
        relative = path.relative_to(boundary.root).as_posix() if path.is_absolute() else name
        record = boundary.read_json(relative)
        if record.get('binding') != binding or record.get('status') != 'passed' or not record.get('reviewer') or not record.get('finishedAt'):
            raise ValueError('current reviewer/time/task binding missing')
        release = record.get('release', {})
        for field in ('bundleId', 'version', 'environment', 'signatureMetadata', 'profileMetadata'):
            if not release.get(field):
                errors.append(f'final artifact {field} missing')
        if not isinstance(release.get('signatureMetadata'), dict) or not isinstance(release.get('profileMetadata'), dict):
            errors.append('signature/Profile must contain reviewed metadata, not a bare passed flag')
        if release.get('buildMode') != 'release':
            errors.append('artifact is not Release')
        artifact = release.get('artifact', {})
        if Path(artifact.get('path', '')).suffix.lower() not in {'.hap', '.app', '.ipa'}:
            errors.append('final artifact must be a distributable application package')
        if not artifact.get('sha256') or hashlib.sha256(boundary.read_bytes(artifact.get('path', ''))).hexdigest() != artifact['sha256']:
            errors.append('final artifact missing/changed')
        for field in ('deviceEvidence', 'regressionEvidence'):
            items = release.get(field, [])
            if not items:
                errors.append(f'{field} missing; mark unverified instead of inventing a pass')
            for item in items:
                if item.get('binding') != binding or item.get('artifactSha256') != artifact.get('sha256') or not item.get('sha256') or hashlib.sha256(boundary.read_bytes(item.get('path', ''))).hexdigest() != item['sha256']:
                    errors.append(f'{field} stale or not bound to current build')
    except (OSError, ValueError, TypeError, KeyError) as error:
        errors.append(f'release evidence: {error}')
    return errors
