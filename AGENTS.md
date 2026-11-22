# Agent Instructions

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

Never assume that GitHub is unavailable just because a tool is missing. In this
environment the available GitHub MCP tools are read-only, `gh` is often not
installed, and `GH_TOKEN`/`GITHUB_TOKEN` are usually unset, yet Git Credential
Manager normally holds a valid credential for `github.com`. Resolve access in
this order:

1. Use the GitHub MCP tools when they expose the operation you need.
2. Use the `gh` CLI if it is installed and authenticated (`gh auth status`).
3. Otherwise use the GitHub REST API with the credential Git already uses for
   `origin`, as described below.

Report which route you used, and verify every remote write by reading the
affected resource back.

### REST fallback

- Resolve the host from `git remote get-url origin` and only send the credential
  to that host's API; for this repository that is `api.github.com`.
- Read the credential from Git's credential helper instead of asking the operator
  for a token. Disable interactive prompts so the call fails instead of blocking:

  ```powershell
  $env:GCM_INTERACTIVE = "Never"
  $env:GIT_TERMINAL_PROMPT = "0"
  $raw = ("protocol=https`nhost=github.com`n`n" | git credential fill 2>$null)
  $token = ($raw -split "`n" | Where-Object { $_ -like "password=*" } |
            Select-Object -First 1) -replace "^password=", ""
  ```

- Verify the account and its scopes before the first write with `GET /user` and
  inspect the `x-oauth-scopes` response header. `repo` is required for issues,
  pull requests and pushing branches. If the credential or the required scope is
  missing, stop and report it.
- Set TLS 1.2 on Windows PowerShell 5.1, use `Invoke-WebRequest -UseBasicParsing`,
  and send a user agent plus the JSON media type:

  ```powershell
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  $headers = @{
      Authorization = "Bearer $token"
      Accept        = "application/vnd.github+json"
      "User-Agent"  = "openhab-garmin-agent"
  }
  ```

- Send JSON bodies as UTF-8 bytes with `-ContentType "application/json; charset=utf-8"`;
  Windows PowerShell 5.1 otherwise mangles non-ASCII characters.
- Build JSON from plain .NET strings. `Get-Content -Raw` returns a decorated
  string that `ConvertTo-Json` serializes as `{"value": ..., "ReadCount": 1}`,
  which GitHub rejects with `422 Unprocessable Entity`. Use
  `[System.IO.File]::ReadAllText()` for file content.
- Use only the endpoint the operation needs. Common calls for this repository:

  | Operation | Request |
  | --- | --- |
  | Read an issue | `GET /repos/openhab/openhab-garmin/issues/{number}` |
  | Comment on an issue or pull request | `POST /repos/openhab/openhab-garmin/issues/{number}/comments` with `{"body": "..."}` |
  | Close an issue as completed | `PATCH /repos/openhab/openhab-garmin/issues/{number}` with `{"state": "closed", "state_reason": "completed"}` |
  | Delete a comment | `DELETE /repos/openhab/openhab-garmin/issues/comments/{comment_id}` |
  | Open a pull request | `POST /repos/openhab/openhab-garmin/pulls` with `{"title": ..., "head": ..., "base": "main", "body": ...}` |

- The credential belongs to the operator's account and carries their permissions.
  Use it only for the operation the operator explicitly authorized (commenting,
  closing, pushing, releases, and so on). If no credential is available, stop and
  ask the operator. Never ask for a token in chat, do not look for one in
  configuration files or other credential stores, and never print, log, write to
  a file, or commit the token.
- Never post probe, test or diagnostic comments on real issues or pull requests.
  Verify access with read endpoints. If a probe was created by mistake, delete it
  immediately and confirm only the intended content remains.

## Branches, pull requests and pushes

Never create a branch, push a branch, or open a pull request on your own
initiative. Do these only when the operator explicitly asks for them.

- Commit locally when the operator asks for a commit, and stop there unless a
  push was requested as well. Commit to `main` only with explicit operator
  authorization.
- If no branch or pull request was authorized and a direct push to `main` is
  rejected, report the rejection to the operator instead of creating a branch.
- Do not push to `main`: this repository has an active `main` branch PR ruleset.
  When the operator authorizes a pull request, push the commit to a feature
  branch and open the pull request against `main`, unless the operator
  explicitly instructs otherwise.
- Branch names follow `<issue>-<short-description>` (see `CONTRIBUTING.md`); use a
  descriptive name when there is no issue.

To prepare a feature branch from an up-to-date `main` without accidentally
pushing to it:

```powershell
git switch -c <issue>-<short-description> origin/main
git branch --unset-upstream      # never inherit main as the push target
git push -u origin HEAD
```

After pushing an authorized branch, open the pull request only if that was
requested, and report its URL.
