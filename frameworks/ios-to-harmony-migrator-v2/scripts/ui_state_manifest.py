#!/usr/bin/env python3
"""Create and validate per-screen UI/interaction state evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from ui_evidence import validate_manifest


DEFAULT_STATES = ("default", "loading", "empty", "error", "return-restore", "dark-mode", "large-text")


def load(path: Path) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"schemaVersion": "1.0", "screens": []}


def evidence_exists(value: str, base: Path) -> bool:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).is_file()


def validate(data: dict, base: Path, slice_id: str | None = None, current_binding: dict | None = None, expected: dict | None = None) -> dict:
    """旧路径清单保持可读取，但缺少新证据契约时只能报告未验证。"""
    return validate_manifest(data, base, slice_id, current_binding, expected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("manifest", type=Path)
    add.add_argument("--slice", required=True)
    add.add_argument("--screen", required=True)
    add.add_argument("--source-ref", required=True)
    add.add_argument("--target-ref", required=True)
    add.add_argument("--states", default=",".join(DEFAULT_STATES))
    check = sub.add_parser("check")
    check.add_argument("manifest", type=Path)
    check.add_argument("--slice")
    check.add_argument("--report", type=Path)
    check.add_argument('--expected', type=Path, help='批准范围导出的独立预期覆盖 JSON')
    check.add_argument('--project', type=Path)
    check.add_argument('--task-id')
    check.add_argument('--profile', default='T3')
    args = parser.parse_args()
    manifest = args.manifest.expanduser().absolute()
    if args.command == 'check' and not (args.project and args.task_id and args.expected):
        print(json.dumps({'status': 'blocked', 'errors': ['legacy evidence is unverified; supply --project --task-id --expected for current binding and approved coverage']}, ensure_ascii=False, indent=2))
        return 1
    data = load(manifest) if args.command == 'add' else {}
    if args.command == "add":
        duplicate = any(item.get("sliceId") == args.slice and item.get("screenId") == args.screen for item in data["screens"])
        if duplicate:
            raise SystemExit("screen already exists for this slice")
        states = [value.strip() for value in args.states.split(",") if value.strip()]
        data["screens"].append({
            "sliceId": args.slice,
            "screenId": args.screen,
            "sourceRef": args.source_ref,
            "targetRef": args.target_ref,
            "states": [{"id": state, "status": "pending", "sourceEvidence": [], "targetEvidence": [], "interactionEvidence": [], "notes": ""} for state in states],
        })
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(manifest)
        return 0
    from read_boundary import require_complete, task_read_scope
    from ui_evidence import load_context, argument_path
    current, expected = None, None
    if args.project and args.task_id and args.expected:
        project = args.project.resolve()
        manifest = argument_path(args.manifest, args.project, project)
        expected_path = argument_path(args.expected, args.project, project)
        with task_read_scope(project, args.task_id):
            boundary = require_complete(project)
            data = boundary.read_json(manifest.relative_to(project).as_posix())
            current, expected = load_context(project, args.task_id, args.profile, args.slice, expected_path)
    report = validate(data, manifest.parent, args.slice, current, expected)
    if args.report:
        destination = args.report.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
