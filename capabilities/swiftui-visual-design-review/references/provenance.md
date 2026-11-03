# Provenance and update policy

## Reviewed upstream

- Repository: `https://github.com/Wholiver/swiftui-design-skill`
- Reviewed commit: `2c82638ebd3c801d9d2d12b5f2d6c20495939995`
- Upstream version: `1.0.0`
- License: MIT
- Review date: 2026-09-21

The adaptation is newly written for this framework suite rather than a verbatim copy. It retains the useful goals of deliberate design direction, brand-aware SwiftUI work, native icons, meaningful signature details, and rendered review.

## Deliberate differences

- Existing project branding overrides generic aesthetic preferences; purple-blue gradients are not banned.
- A single clear direction is allowed when intent is established; two or three variants are not mandatory.
- The project deployment target controls API availability; iOS 18 is not required.
- Vibe Playbook, daily-development, and migration frameworks retain orchestration and verification ownership.
- Migration source remains read-only and parity work cannot become a redesign.
- No fixed font count, color count, spacing grid, or numerical design score is required.
- No scripts, hooks, network calls, or third-party runtime dependencies are included.

## Updating

Do not merge upstream changes blindly. Before updating:

1. record the new repository commit and review date;
2. inspect changes to `SKILL.md`, references, scripts, hooks, network access, write paths, and license;
3. preserve the deliberate differences above;
4. validate the adapted skill and test at least one realistic SwiftUI visual request;
5. update this file with only the upstream ideas actually adopted.
