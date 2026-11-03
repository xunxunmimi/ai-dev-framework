# SwiftUI visual review

Use this reference for a substantial screen redesign or an explicit UI-quality audit. Review only the affected surface and its direct states.

## 1. Product and content hierarchy

- Can a user identify the screen's purpose and primary action without reading every label?
- Does the first viewport prioritize the core task instead of decorative summaries?
- Are secondary controls progressively disclosed rather than competing with the primary action?
- Is terminology understandable to the target user rather than written from the implementation's point of view?

## 2. Brand coherence and distinctiveness

- Reuse verified project colors, typography, icon language, shapes, and motion character.
- Identify one product-specific visual or interaction idea worth emphasizing; remove unrelated flourishes.
- Check that launch, app icon, navigation, empty states, paywalls, and settings feel like the same product when they are in scope.
- Do not replace a coherent brand merely to follow a generic trend or an upstream skill's aesthetic preference.

## 3. Layout and typography

- Use semantic type roles and Dynamic Type rather than fixed sizes that clip.
- Check spacing rhythm and alignment across adjacent sections; a strict grid is optional, consistency is not.
- Keep line length, truncation, wrapping, and localization expansion practical.
- Ensure large titles, cards, safe areas, keyboards, sheets, and tab bars do not create accidental dead space or overlap.

## 4. Controls and interaction states

- Use native control semantics for buttons, toggles, menus, navigation, selection, and destructive actions.
- Make pressed, selected, disabled, loading, success, error, and retry states distinguishable when relevant.
- Keep important actions reachable and reversible where the product permits.
- Do not hide essential actions behind unlabeled gestures.

## 5. Motion and feedback

- Every animation needs a trigger, visual subject, and completion state.
- Prefer one dominant motion event over several unrelated looping effects.
- Preserve interaction during interruption and rapid repeated input.
- Provide Reduce Motion or reduced-effects behavior for nonessential transforms, parallax, blur, and looping animation.
- Use haptics only for meaningful confirmation or selection, not decoration.

## 6. Accessibility and appearance

- Check readable contrast in light and dark appearance.
- Check Dynamic Type at a representative accessibility size for the changed surface.
- Check VoiceOver labels, traits, reading order, and whether decorative content is hidden.
- Communicate state with text or shape in addition to color.
- Treat 44×44 points as the normal target for touch controls; document justified exceptions.

## 7. Evidence

Choose evidence proportional to the primary framework's task level:

- targeted build for compile and asset integration;
- rendered screenshot at the affected state for visual changes;
- interaction smoke test for navigation, gestures, keyboard, or animation;
- light/dark or Dynamic Type checks when the change plausibly affects them;
- device verification only when hardware behavior or fidelity matters.

Report observed evidence separately from inference. A build proves compilation, not visual quality; one screenshot proves one state, not the complete interaction.
