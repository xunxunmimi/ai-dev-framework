"""Candidate for formally recording an already-approved literal permission.

Requires existing platform approval, externally verified writer quiescence, and
an exact independently reviewed plan. Explicit CLI: restyle_scope_cli.py.
The CLI records evidence; it never grants platform permission.
"""
from __future__ import annotations
import contextlib
import fcntl
import hashlib
import os
from pathlib import Path
import secrets
import stat
from read_boundary import ReadBoundary, bootstrap_bytes, secure_fd, POLICY_PATH
from task_runtime import root_anchor
from exact_restyle_scope import preview
from transition_scope_preview import preview as preview_transition

LOCKS = (".ai-project/framework-update.lock", ".ai-project/tasks.lock",
         ".ai-project/reimplementation-install.lock")
MANIFEST = ".ai-project/framework-installation.json"
FILES_MUST_BE_ABSENT = (".ai-project/identity.json", ".ai-project/operation-holds.json",
                        ".ai-project/legacy-baseline.json", ".ai-project/task-input.json")
DIRS_MUST_BE_EMPTY = (".ai-project/tasks", ".ai-project/upgrades", ".ai-project/checkpoints")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def identity(fd):
    s = os.fstat(fd)
    return s.st_dev, s.st_ino


def register(root, approved_before, approved_after, installed_manifest_sha,
             owner_approval_reference, stopped_evidence, *, nonce=None, approved_exact_delta=None, fault=None):
    if not isinstance(stopped_evidence, str) or not stopped_evidence.strip():
        raise ValueError("Write activity must be independently observed stopped")
    root = Path(root).absolute()
    b = ReadBoundary(root)
    with contextlib.ExitStack() as stack:
        locks = []
        for name in LOCKS:
            b.require(name)
            fd = secure_fd(root, name)
            stack.callback(os.close, fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locks.append((name,fd))
        if nonce is None:
            proposal = preview(root, approved_before, owner_approval_reference)
            delta = proposal["newExactReadPaths"]
        else:
            if not isinstance(owner_approval_reference,str) or not owner_approval_reference.strip():
                raise ValueError("Record the known owner approval")
            proposal = preview_transition(root, nonce, approved_before)
            delta = proposal["exactReadAdditions"]
            if not isinstance(approved_exact_delta, (tuple,list)) or sorted(approved_exact_delta) != delta:
                raise ValueError("The exact permission delta was not approved")
        after = proposal["afterPolicyText"].encode()
        if proposal["policyAfterSha256"] != approved_after:
            raise ValueError("Exact recorded policy proposal not approved")
        before = bootstrap_bytes(root, POLICY_PATH)
        anchor = root_anchor(root)
        parent = secure_fd(root, ".ai-project", directory=True)
        stack.callback(os.close, parent)

        def validate():
            p = secure_fd(root, ".ai-project", directory=True)
            try:
                if identity(p) != identity(parent) or root_anchor(root) != anchor:
                    raise ValueError("Project or policy parent changed")
            finally:
                os.close(p)
            for name, expected in locks:
                fd = secure_fd(root, name)
                try:
                    if identity(fd) != identity(expected):
                        raise ValueError("An existing lock path changed")
                finally:
                    os.close(fd)
            if bootstrap_bytes(root, POLICY_PATH) != before or sha(before) != approved_before:
                raise ValueError("Policy changed before publication")
            if sha(b.read_bytes(MANIFEST)) != installed_manifest_sha:
                raise ValueError("Original installation manifest changed")
            for name in FILES_MUST_BE_ABSENT:
                if b.exists(name):
                    raise ValueError("Existing task or governance history")
            for name in DIRS_MUST_BE_EMPTY:
                b.require(name)
                try:
                    fd = secure_fd(root, name, directory=True)
                except FileNotFoundError:
                    continue
                try:
                    if os.listdir(fd):
                        raise ValueError("Existing task or governance history")
                finally:
                    os.close(fd)

        validate()
        name = "read-policy-exact-preimage-" + approved_before + ".json"
        b.require(".ai-project/" + name)
        stage = ".read-policy-exact-stage-" + secrets.token_hex(12)
        b.require(".ai-project/" + stage)
        backup = ".read-policy-exact-backup-" + secrets.token_hex(12)
        b.require(".ai-project/" + backup)
        def temporary(file, data, mode):
            fd = os.open(file, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, mode, dir_fd=parent)
            with os.fdopen(fd, "wb") as out:
                os.fchmod(out.fileno(), mode)
                out.write(data); out.flush(); os.fsync(out.fileno())

        # Archived original bytes are durable before any live policy replacement.
        temporary(backup, before, 0o600)
        try:
            try:
                os.link(backup,name,src_dir_fd=parent,dst_dir_fd=parent,follow_symlinks=False)
                os.fsync(parent)
            except FileExistsError:
                if b.read_bytes(".ai-project/" + name) != before:
                    raise ValueError("Existing preimage differs")
        finally:
            os.unlink(backup, dir_fd=parent); os.fsync(parent)
        if fault:
            fault("archive-durable")
        original = os.stat("read-policy.json",dir_fd=parent,follow_symlinks=False)
        temporary(stage, after, stat.S_IMODE(original.st_mode))
        try:
            if fault:
                fault("staged")
            validate()
            os.replace(stage,"read-policy.json",src_dir_fd=parent,dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            try: os.unlink(stage,dir_fd=parent)
            except FileNotFoundError: pass
        if sha(bootstrap_bytes(root,POLICY_PATH)) != approved_after:
            raise ValueError("Publication not verified; stop")
        return {"status":"registered-exact-parent","policySha256":approved_after,
                "newExactReadPaths":delta, "authorityClaim":False,
                "dailyTransitionApplied":False}
