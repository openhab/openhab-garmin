# Copilot Instructions

## Do not create unit tests

Do not create or add automated tests, including Connect IQ `(:test)` unit tests, test source folders, or test infrastructure in `monkey.jungle`.
This repository does not use automated unit tests at this time.

Instead of writing tests, provide the operator with clear, step-by-step instructions to verify the change manually in the Garmin Connect IQ simulator.
The instructions should cover the relevant device/input models where applicable (for example touch, button, and Edge devices) and the expected behavior, including edge cases.

If a change would normally warrant an automated test, state the manual verification steps explicitly and let the operator run them.

## Keep documentation in sync

Keep inline documentation, `README.md` files, and `docs/USAGE.md` in line with every change:

- Update affected inline comments and class-level documentation.
- Update the relevant `README.md` files when changing structure, behavior, or conventions.
- Update `docs/USAGE.md` when changing user-facing behavior or supported sitemap elements.

If you notice existing inconsistencies between the code and the documentation that are outside the scope of the current change, point them out to the operator instead of silently ignoring them or fixing unrelated content.

## Commit messages

If the work relates to a GitHub issue, reference the issue number in the commit message, for example `Fixes #343` or `Refs #343`.

## Commits on the main branch

When working on the `main` branch, commit only with explicit operator authorization.

