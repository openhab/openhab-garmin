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

## GitHub access (issues, pull requests, releases)

Prefer dedicated tooling for GitHub operations:

1. Use the GitHub MCP tools when they expose the operation you need.
2. Use the `gh` CLI if it is installed and authenticated.
3. If neither is available (for example, the MCP server exposes only read tools and
   `gh` is not installed), fall back to the GitHub REST API with the credential Git
   already uses for `origin`.

For the REST fallback:

- Resolve the host from `git remote get-url origin`. Only send the credential to
  the API of that host; for this repository that is `api.github.com`.
- Read the credential from Git's credential helper instead of asking the operator
  for a token:

  ```powershell
  $env:GCM_INTERACTIVE = "never"
  $env:GIT_TERMINAL_PROMPT = "0"
  $raw = ("protocol=https`nhost=github.com`n`n" | git credential fill 2>$null)
  $token = ($raw -split "`n" | Where-Object { $_ -like "password=*" } |
            Select-Object -First 1) -replace "^password=", ""
  ```

- The credential belongs to the operator's account and carries their permissions.
  Use it only for the requested operation, and only after the operator explicitly
  authorized that remote action (commenting, closing, pushing, releases, and so on).
- If no credential is available, stop and ask the operator. Never ask for a token
  in chat, and do not look for one in configuration files or other credential stores.
- Never print, log, write to a file, or commit the token.
- Before a write, verify the authenticated account and its permissions with
  `GET /user` and `GET /repos/{owner}/{repo}`. If the required permission is
  missing, report it instead of continuing.
- Send JSON bodies as UTF-8 bytes. Windows PowerShell 5.1 otherwise mangles
  non-ASCII characters, and GitHub rejects the request with
  `400 Problems parsing JSON`.
- Verify remote writes afterwards, for example by reading the issue state back or
  by keeping the URL of the created comment.

## Commits on the main branch

When working on the `main` branch, commit only with explicit operator authorization.

Do not push directly to `main`: this repository has an active `main` branch PR
ruleset. Push the commit to a feature branch and open a pull request instead,
unless the operator explicitly instructs otherwise.

