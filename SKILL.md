---
name: clockify-zoho-sync
description: Sync Clockify time entries into Zoho People timesheet entries (timetracker). Use when the user asks to sync/push Clockify time into Zoho People, log Clockify hours to Zoho, or update the Zoho timesheet from Clockify.
---

# Clockify -> Zoho People sync

Pulls Clockify time entries for a date/range and writes them straight as
Zoho People timelog entries (`addtimelog`) -- no mapping file, no setup
beyond env vars. Zoho project = Clockify project name, Zoho job = the
entry's description (Zoho auto-creates both on first use). **Always
dry-runs first** -- never pushes without a preview being shown and the
user explicitly asking to proceed.

Tradeoff of zero-config: every distinct Clockify description becomes its
own permanent Zoho job. Encourage the user to reuse consistent wording
for the same kind of work rather than writing a fresh description every
time.

## Required env vars

| Var | Notes |
|---|---|
| `CLOCKIFY_API_KEY` | From Clockify profile settings. Dies periodically -- if calls 401 with `code 4003`, ask the user for a fresh one, don't debug workspace/user IDs first. |
| `CLOCKIFY_WORKSPACE_ID` | |
| `CLOCKIFY_USER_ID` | Whose entries to pull. |
| `ZOHO_CLIENT_ID` | From a Self Client app in the Zoho API Console matching the user's data center. |
| `ZOHO_CLIENT_SECRET` | Same app. |
| `ZOHO_REFRESH_TOKEN` | Long-lived; obtained once via the Self Client grant-code flow (see below), doesn't expire until revoked. |
| `ZOHO_PEOPLE_EMAIL` | The Zoho People login used as the API `user` param -- confirm with the user, don't assume it matches their Clockify/Azure email. |
| `ZOHO_ACCOUNTS_DOMAIN` | Optional, default `accounts.zoho.com`. Use `accounts.zoho.eu` / `.in` / etc. to match the user's DC. |
| `ZOHO_PEOPLE_DOMAIN` | Optional, default `people.zoho.com`. Same DC rule. |

If any of the Zoho vars are missing, walk the user through Self Client
setup rather than guessing:

1. `https://api-console.zoho.com` (region-matched) -> **Add Client** -> **Self Client**.
2. Save Client ID + Secret.
3. **Generate Code** tab, scope `ZOHOPEOPLE.timetracker.ALL`, max duration, Create.
4. Exchange the one-time grant code fast (expires in minutes):
   ```
   curl -s -X POST "https://accounts.zoho.com/oauth/v2/token" \
     -d grant_type=authorization_code \
     -d client_id=$ZOHO_CLIENT_ID \
     -d client_secret=$ZOHO_CLIENT_SECRET \
     -d code=<grant code>
   ```
   Save the returned `refresh_token` as `ZOHO_REFRESH_TOKEN`.

Do not write any of these to a file. Env vars only.

## Usage

```
python scripts/sync.py                          # today, dry run
python scripts/sync.py --date 2026-09-10         # one day, dry run
python scripts/sync.py --start 2026-09-01 --end 2026-09-14   # range, dry run
python scripts/sync.py --range yesterday         # preset range, dry run
python scripts/sync.py --date 2026-09-14 --push  # actually writes
```

`--range` presets: `today`, `yesterday`, `this-week`, `last-week`,
`this-month`, `last-month`. Weeks start Monday. Takes priority over
`--date`/`--start`/`--end` if both given. Also callable directly from
PowerShell as `zoho-sync --range today` (function in `$PROFILE`, wraps
this script -- see profile block for env vars it sets).

Every run (including `--push`) prints:
- entries that will be pushed, grouped, with total hours
- entries already present in Zoho for that date/job/description (skipped, not re-pushed)
- entries whose Clockify project isn't in `config/mapping.json` (skipped)
- entries whose description matched no rule for a known project (skipped, "unmapped")

**Show this preview to the user and get explicit confirmation before
re-running with `--push`.** Don't chain dry-run and `--push` in one go.

## Known gotchas

- `CLOCKIFY_API_KEY` goes stale silently (401, `{"code":4003}`) -- ask for
  a fresh key immediately rather than debugging further.
- Zoho jobs can share a name across different projects (e.g. same
  description logged under two different Clockify projects) -- the
  script disambiguates by `(projectName, jobName)`, never jobName alone.
  Keep that in mind if you edit `sync.py`.
- An entry with no Clockify project attached is skipped (nowhere to file
  it in Zoho) -- shown in the preview under "No project on entry".
- Zoho `edittimelog` looks like a partial-patch endpoint from the docs
  but actually requires every field to be resent (hours, workDate,
  jobId, billingStatus) or it errors -- always resend the full set.
- This tool writes to a real, shared HR system. Treat it like any other
  hard-to-reverse action: preview, confirm, then push.
