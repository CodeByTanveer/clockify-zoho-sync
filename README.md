# clockify-zoho-sync

Pulls Clockify time entries and pushes them as Zoho People timelog
entries. Zero mapping/config -- Zoho project = Clockify project name,
Zoho job = entry description, both auto-created by Zoho on first use.

Full docs, env vars, and OAuth setup walkthrough: [SKILL.md](SKILL.md).

## Quick start

```
git clone https://github.com/CodeByTanveer/clockify-zoho-sync
cd clockify-zoho-sync
# set env vars -- see SKILL.md "Required env vars" table
python scripts/sync.py --range today          # dry run
python scripts/sync.py --range today --push   # writes to Zoho
```

## Use as a Claude Code skill

Copy (or symlink) this folder into `~/.claude/skills/clockify-zoho-sync/`
and Claude will pick it up automatically -- `SKILL.md` carries the
frontmatter Claude reads to know when to use it.

## Tradeoff

Every distinct Clockify description becomes its own permanent Zoho job.
Keep descriptions consistent for the same kind of recurring work.
