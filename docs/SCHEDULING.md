# Scheduling — External Clock Setup

GitHub's `schedule` trigger proved unusable on this repo (7–9h drift, then
~95% of events silently dropped). `workflow_dispatch` starts within seconds,
every time. So an external clock POSTs the dispatch API twice daily.

## One-time setup (~15 minutes)

### Step 1 — Fine-grained token (github.com/settings/personal-access-tokens)
- Repository access: **Only select repositories → fintwit-scanner**
- Permissions: **Actions → Read and write**. Nothing else.
- Expiry: 90 days. Copy the token once.

This same act retires the old broad `ghp_` token — revoke it at
github.com/settings/tokens as the LAST step below.

### Step 2 — Two cron jobs at cron-job.org (free account)

Both jobs identical except schedule and body:

- **URL:** `https://api.github.com/repos/kripa-gh/fintwit-scanner/actions/workflows/daily_scan.yml/dispatches`
- **Method:** POST
- **Headers:**
  - `Authorization: Bearer <YOUR NEW TOKEN>`
  - `Accept: application/vnd.github+json`
  - `Content-Type: application/json`
- **Job A (pre-market):** schedule 03:00 UTC, Mon–Fri, body:
  `{"ref":"main","inputs":{"mode":"premarket"}}`
- **Job B (main scan):** schedule 10:31 UTC, Mon–Fri, body:
  `{"ref":"main","inputs":{"mode":"main"}}`
- Enable failure notifications on both jobs (cron-job.org emails you if
  GitHub ever answers non-2xx — that's the watchdog).

Expected success response: HTTP 204, empty body.

### Alternative to Step 2 — Google Apps Script (no new account)
If you'd rather not create a cron-job.org account: script.google.com → new
project → paste `tools/apps_script_scheduler.gs` from this repo → fill in the
token constant → run `setupTrigger()` once and grant permissions. It checks
every 15 minutes and dispatches inside the windows with built-in dedupe.
Precision: ≤15 min. cron-job.org is preferred for exact timing.

### Step 3 — Test
Trigger Job B manually once ("test run"). A report email should arrive in
~5 minutes. Check the run appears at Actions with event `workflow_dispatch`.

### Step 4 — Finish
1. Merge the PR that removes the `schedule:` block (this file ships with it).
2. Revoke the old `ghp_` classic token at github.com/settings/tokens.

## Why not GitHub cron, measured
| Design | Result |
|---|---|
| Plain cron 03:02 / 10:37 UTC | Fired 7–9h late daily (11:44–12:48 / 16:41–18:27 UTC) |
| Poll every 15 min + IST gate | Only 4–5 of ~96 daily events delivered, random hours; zero landed in windows |
| workflow_dispatch | Started within seconds, 100% of recorded attempts |
