#!/usr/bin/env python
"""Sync Clockify time entries into Zoho People timesheet (timetracker) entries.

Always previews before writing. Nothing is pushed to Zoho unless --push is
passed AND the preview was already shown (run once without --push, review,
then re-run with --push).

Required env vars:
  CLOCKIFY_API_KEY
  CLOCKIFY_WORKSPACE_ID
  CLOCKIFY_USER_ID
  ZOHO_CLIENT_ID
  ZOHO_CLIENT_SECRET
  ZOHO_REFRESH_TOKEN
  ZOHO_PEOPLE_EMAIL        # the Zoho People login used as the "user" param
  ZOHO_ACCOUNTS_DOMAIN     # optional, default accounts.zoho.com
  ZOHO_PEOPLE_DOMAIN       # optional, default people.zoho.com

No mapping file, no setup beyond env vars. Each Clockify entry becomes a
Zoho timelog directly: Zoho project = Clockify project name, Zoho job =
the entry's (first) tag name, Zoho work item/description = the entry's
description. An entry with no tag is skipped (nowhere to file it as a
Zoho job). Zoho auto-creates the project/job on first use if they don't
already exist. Tradeoff: every distinct tag becomes its own permanent
Zoho job -- keep tagging consistent (one tag per kind of work) to avoid
cluttering the Zoho job list.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

CLOCKIFY_BASE = "https://api.clockify.me/api/v1"


def env(name, required=True, default=None):
    val = os.environ.get(name, default)
    if required and not val:
        sys.exit(f"Missing required env var: {name}")
    return val


def http(method, url, headers=None, data=None, form=False):
    headers = dict(headers or {})
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        else:
            body = json.dumps(data).encode()
            headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        sys.exit(f"{method} {url} -> {e.code}\n{detail}")


# ---------- Clockify ----------

def clockify_headers():
    return {"X-Api-Key": env("CLOCKIFY_API_KEY")}


def fetch_clockify_entries(start_iso, end_iso):
    workspace = env("CLOCKIFY_WORKSPACE_ID")
    user = env("CLOCKIFY_USER_ID")
    url = (
        f"{CLOCKIFY_BASE}/workspaces/{workspace}/user/{user}/time-entries"
        f"?start={start_iso}&end={end_iso}&page-size=200"
    )
    return http("GET", url, headers=clockify_headers())


def fetch_clockify_projects():
    workspace = env("CLOCKIFY_WORKSPACE_ID")
    url = f"{CLOCKIFY_BASE}/workspaces/{workspace}/projects?page-size=200"
    projects = http("GET", url, headers=clockify_headers())
    return {p["id"]: p["name"] for p in projects}


def fetch_clockify_tags():
    workspace = env("CLOCKIFY_WORKSPACE_ID")
    url = f"{CLOCKIFY_BASE}/workspaces/{workspace}/tags?page-size=200"
    tags = http("GET", url, headers=clockify_headers())
    return {t["id"]: t["name"] for t in tags}


# ---------- Zoho ----------

def zoho_access_token():
    domain = env("ZOHO_ACCOUNTS_DOMAIN", required=False, default="accounts.zoho.com")
    data = {
        "grant_type": "refresh_token",
        "client_id": env("ZOHO_CLIENT_ID"),
        "client_secret": env("ZOHO_CLIENT_SECRET"),
        "refresh_token": env("ZOHO_REFRESH_TOKEN"),
    }
    resp = http("POST", f"https://{domain}/oauth/v2/token", data=data, form=True)
    if "access_token" not in resp:
        sys.exit(f"Zoho token refresh failed: {resp}")
    return resp["access_token"]


def zoho_headers(token):
    return {"Authorization": f"Zoho-oauthtoken {token}"}


def people_base():
    domain = env("ZOHO_PEOPLE_DOMAIN", required=False, default="people.zoho.com")
    return f"https://{domain}/people/api/timetracker"


def fetch_zoho_jobs(token, assigned_to):
    url = f"{people_base()}/getjobs?assignedTo={urllib.parse.quote(assigned_to)}"
    resp = http("GET", url, headers=zoho_headers(token))
    return resp.get("response", {}).get("result", []) or []


def fetch_zoho_timelogs(token, user, from_date, to_date):
    url = (
        f"{people_base()}/gettimelogs?user={urllib.parse.quote(user)}"
        f"&fromDate={from_date}&toDate={to_date}&dateFormat=yyyy-MM-dd"
    )
    resp = http("GET", url, headers=zoho_headers(token))
    return resp.get("response", {}).get("result", []) or []


def push_zoho_timelog(
    token, user, work_date, hours_hhmm, text, job_id=None, job_name=None, project_name=None
):
    """Push a timelog. Pass job_id for an existing job, or job_name +
    project_name to have Zoho auto-create that job (and project, if it
    doesn't exist yet) -- per Zoho's own addtimelog semantics."""
    url = f"{people_base()}/addtimelog"
    data = {
        "user": user,
        "workDate": work_date,
        "hours": hours_hhmm,
        "billingStatus": "Billable",
        "description": text,
        "workItem": text,
    }
    if job_id:
        data["jobId"] = job_id
    else:
        data["jobName"] = job_name
        data["projectName"] = project_name
    resp = http("POST", url, headers=zoho_headers(token), data=data, form=True)
    return resp


# ---------- helpers ----------

def parse_iso_duration_minutes(iso):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", iso)
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    return hours * 60 + minutes


def minutes_to_hhmm(total_minutes):
    return f"{total_minutes // 60}:{total_minutes % 60:02d}"


def normalize_text(s):
    return re.sub(r"\s+", " ", s or "").strip().lower()


def already_synced(existing_entries, work_date, job_name, description_text):
    target = normalize_text(description_text)
    for e in existing_entries:
        if (
            e.get("workDate") == work_date
            and e.get("jobName") == job_name
            and normalize_text(e.get("description")) == target
        ):
            return True
    return False


RANGE_PRESETS = ["today", "yesterday", "this-week", "last-week", "this-month", "last-month"]


def resolve_range(name):
    """Preset date ranges. Weeks start Monday (matches this Clockify
    workspace's weekStart setting)."""
    today = dt.date.today()
    if name == "today":
        return today.isoformat(), today.isoformat()
    if name == "yesterday":
        y = today - dt.timedelta(days=1)
        return y.isoformat(), y.isoformat()
    if name == "this-week":
        start = today - dt.timedelta(days=today.weekday())
        return start.isoformat(), today.isoformat()
    if name == "last-week":
        this_monday = today - dt.timedelta(days=today.weekday())
        last_monday = this_monday - dt.timedelta(days=7)
        last_sunday = this_monday - dt.timedelta(days=1)
        return last_monday.isoformat(), last_sunday.isoformat()
    if name == "this-month":
        start = today.replace(day=1)
        return start.isoformat(), today.isoformat()
    if name == "last-month":
        first_this_month = today.replace(day=1)
        last_day_prev = first_this_month - dt.timedelta(days=1)
        first_day_prev = last_day_prev.replace(day=1)
        return first_day_prev.isoformat(), last_day_prev.isoformat()
    sys.exit(f"Unknown --range {name!r}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--range",
        choices=RANGE_PRESETS,
        help="preset date range, e.g. today/yesterday/this-week/last-week/"
        "this-month/last-month (overrides --date/--start/--end)",
    )
    ap.add_argument("--date", help="single day, yyyy-mm-dd (default: today)")
    ap.add_argument("--start", help="range start, yyyy-mm-dd")
    ap.add_argument("--end", help="range end, yyyy-mm-dd (inclusive)")
    ap.add_argument(
        "--push",
        action="store_true",
        help="actually write to Zoho People. Without this, only previews.",
    )
    args = ap.parse_args()

    if args.range:
        start_date, end_date = resolve_range(args.range)
    elif args.start or args.end:
        if not (args.start and args.end):
            sys.exit("--start and --end must be given together")
        start_date, end_date = args.start, args.end
    else:
        day = args.date or dt.date.today().isoformat()
        start_date = end_date = day

    start_iso = f"{start_date}T00:00:00Z"
    end_iso = f"{end_date}T23:59:59Z"

    zoho_user = env("ZOHO_PEOPLE_EMAIL")

    print(f"Fetching Clockify entries {start_date}..{end_date} ...")
    entries = fetch_clockify_entries(start_iso, end_iso)
    projects = fetch_clockify_projects()
    tags = fetch_clockify_tags()

    token = zoho_access_token()
    print("Fetching existing Zoho People timelog entries (dedupe check) ...")
    existing = fetch_zoho_timelogs(token, zoho_user, start_date, end_date)

    to_push = []
    no_project = []
    no_tag = []
    already_logged = []
    in_progress = []

    for e in entries:
        if e["timeInterval"].get("duration") is None:
            in_progress.append(e)
            continue

        proj_id = e.get("projectId")
        zoho_project_name = projects.get(proj_id)
        if not zoho_project_name:
            no_project.append(e)
            continue

        tag_ids = e.get("tagIds") or []
        job_name = tags.get(tag_ids[0]) if tag_ids else None
        if not job_name:
            no_tag.append(e)
            continue

        description = (e.get("description") or "").strip() or "(no description)"

        minutes = parse_iso_duration_minutes(e["timeInterval"]["duration"])
        work_date = e["timeInterval"]["zonedStart"][:10]

        if already_synced(existing, work_date, job_name, description):
            already_logged.append((e, job_name))
            continue

        to_push.append((e, job_name, zoho_project_name, work_date, minutes, description))

    print()
    print("=== Preview ===")
    if to_push:
        total = 0
        for e, job_name, zoho_project_name, work_date, minutes, description in to_push:
            total += minutes
            print(
                f"  [{work_date}] {minutes//60}h{minutes%60:02d}m  "
                f"{zoho_project_name}/{job_name:28s}  {description}"
            )
        print(f"  -> {len(to_push)} entries, {total//60}h{total%60:02d}m total")
    else:
        print("  Nothing to push.")

    if already_logged:
        print(f"\nAlready synced (skipped, {len(already_logged)}):")
        for e, job_name in already_logged:
            print(f"  [{e['timeInterval']['zonedStart'][:10]}] {job_name}: {e['description']}")

    if no_project:
        print(f"\nNo project on entry, can't determine Zoho project (skipped, {len(no_project)}):")
        for e in no_project:
            print(f"  {e.get('description') or '(no description)'}")

    if no_tag:
        print(f"\nNo tag on entry, can't determine Zoho job (skipped, {len(no_tag)}):")
        for e in no_tag:
            print(f"  {e.get('description') or '(no description)'}")

    if in_progress:
        print(f"\nStill running -- no end time yet (skipped, {len(in_progress)}):")
        for e in in_progress:
            print(f"  [{e['timeInterval']['zonedStart'][:10]}] {e['description'] or '(no description)'}")

    if not to_push:
        return

    if not args.push:
        print("\nDry run only. Re-run with --push to write these to Zoho People.")
        return

    print("\n=== Pushing ===")
    all_jobs = fetch_zoho_jobs(token, zoho_user)
    for e, job_name, zoho_project_name, work_date, minutes, description in to_push:
        job = next(
            (
                j
                for j in all_jobs
                if j["jobName"] == job_name and j.get("projectName") == zoho_project_name
            ),
            None,
        )
        if job:
            resp = push_zoho_timelog(
                token, zoho_user, work_date, minutes_to_hhmm(minutes), description, job_id=job["jobId"]
            )
        else:
            print(f"  (creating job {job_name!r} under Zoho project {zoho_project_name!r} ...)")
            resp = push_zoho_timelog(
                token,
                zoho_user,
                work_date,
                minutes_to_hhmm(minutes),
                description,
                job_name=job_name,
                project_name=zoho_project_name,
            )
        result = resp.get("response", {})
        if result.get("status") == 0:
            time_log_id = result["result"][0]["timeLogId"]
            print(f"  OK  {work_date} {job_name:20s} {description[:60]!r} -> timeLogId {time_log_id}")
        else:
            print(f"  FAIL {work_date} {job_name:20s} {description[:60]!r} -> {result}")


if __name__ == "__main__":
    main()
