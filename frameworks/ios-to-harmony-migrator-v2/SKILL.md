---
name: ios-to-harmony-migrator-v2
description: Initialize, resume, implement, debug, and verify an iOS-to-HarmonyOS migration with risk-based context and tests. Use during active migration and for migration-parity residuals in transition; use HarmonyOS daily rules for ordinary post-migration maintenance.
---

# iOS → HarmonyOS migration V2

Preserve observable product behavior while spending context and verification effort in proportion to risk. Always read [core-contract.md](references/core-contract.md). Do not preload other references.

## Start or resume

- New project: run `python3 scripts/start_migration.py <ios-source> [harmony-target]` and follow the generated `task-context.json`.
- Before any new HarmonyOS business implementation, complete the Phase 0 baseline described in [discovery-gate.md](references/discovery-gate.md), pass `scripts/check_discovery_gate.py`, generate a plan bound to its hashes, and obtain user approval through `scripts/plan_approval.py approve`. Do not bypass this gate with a partial inventory or an untracked assumption.
- Existing V2 project: for an ambiguous or T2+ task, run `python3 scripts/classify_task.py --project <target> --request '<user request>' --stage pre --task-id <initialized-task-id> --write-context`, then read only `mustRead`. Clear T0/T1 tasks may be classified in memory without creating process files.
- Phase changes must use `scripts/migration_state.py`; never edit phase/status fields directly. Read [state-machine.md](references/state-machine.md) when advancing, resuming, blocking, transitioning, or releasing a migration.
- Follow `toolRouting` in `task-context.json`: a non-`off` Lanhu mode permits only the stated read-only design scope; `ponytailReview=post-diff-conditional` means inspect the final diff for complexity signals and invoke `$ponytail-review` at most once only when those signals exist.
- In the same turn, do not reread an unchanged rule, matrix, or status file.

## Route by task

Read [task-routing.md](references/task-routing.md) only when classifying or escalating work.

- T0: documentation or metadata only.
- T1: localized, reversible bug fix with known impact; user performs runtime acceptance by default.
- T2: one page, component, feature, or interaction.
- T3: migration slice, shared contract, dependency, or cross-module change.
- T4: release, identity, payment, security, data migration, or whole-app milestone.

For T0/T1, use the fast lane: inspect the final diff and run only a cheap static or targeted compile check when the change has real compile risk. Do not start simulators, run UI automation, capture screenshots, add tests, or create an independent report by default; state briefly that runtime acceptance is left to the user. Run `scripts/run_gates.py` for T2-T4 with the selected profile. Escalate only when impact, failure, or uncertainty justifies it. A passing targeted check is not a whole-app release claim.

## Conditional guidance

- Full source analysis, plan, or a new migration slice: read [migration-workflow.md](references/migration-workflow.md) and [source-analysis.md](references/source-analysis.md).
- Multiple tester/product reports about iOS consistency or cross-flow parity in `migration-transition`: read [parity-residuals.md](references/parity-residuals.md). Treat the initial T3 as a joint diagnostic umbrella, then reclassify and implement independently verifiable slices; do not restart all of Phase 0 or run whole-app regression by default.
- New migration, new unapproved scope, or changed iOS source: read [discovery-gate.md](references/discovery-gate.md). Existing approved slices and ordinary transition-stage maintenance do not redo Phase 0 unless their source evidence changed.
- ArkTS/ArkUI implementation or compiler repair: read [arkts-implementation.md](references/arkts-implementation.md).
- T2-T4 source implementation or an explicit quality/architecture/comment review: read the project `.migration-v2/references/code-quality-contract.md` and [code-quality-migration.md](references/code-quality-migration.md). T0/T1 use the compact rules in the task router and do not load these files.
- UI or interaction work at T2-T4: read [ui-quality.md](references/ui-quality.md). Use the weighted 90-point review for milestone UI; use affected states only for T2.
- Test selection, Gate configuration, evidence reuse, or release: read [verification.md](references/verification.md); when project commands are missing or changed, also read [gate-configuration.md](references/gate-configuration.md).
- Identity, payment, data migration, security, signing, or release: read [high-risk-and-release.md](references/high-risk-and-release.md).
- Model choice: read [model-policy.md](references/model-policy.md). Model names are configurable guidance, not a promise that a Skill can switch the current task automatically.
- Third-party or daily-development Skills: read [skill-interop.md](references/skill-interop.md) only when installing, updating, or resolving overlapping Skills.

## Completion

For T0/T1, do not create a gate report or evidence bundle; update traceability only if the migration mapping itself changed, and keep the final summary concise. User runtime acceptance may remain pending without marking the code task blocked. For T2-T4, update only the current task record and affected traceability entries. Preserve raw failures. Use `verified` only for the scope actually tested; use `partial` or `blocked` for missing external evidence. New runtime feedback that contradicts `verified` reopens only the affected flow, not the whole migration. Run whole-app regression only for T4, a planned milestone, or a demonstrated cross-cutting risk. In `migration-transition`, ordinary HarmonyOS bugs/features use the HarmonyOS daily framework; use V2 only when the task is about parity, missing iOS behavior, or migration evidence.

For persistent work, first follow the suite `shared/context-engineering/task-control.md`: confirm project identity and initialize one isolated task ID. Gates and state transitions must bind that task, slice, profile, code and plan; never accept a bare passed JSON. New or changed App-owned functions, members/properties/constants and important calculations require accurate Chinese comments for purpose, applicable contracts, units/state and non-obvious boundaries; manually review semantics and exclude generated/third-party code.
