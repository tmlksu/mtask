# HANDOFF — mtask

Working notes for continuing development. User-facing usage lives in `README.md`;
this file captures architecture, conventions, and what's next.

## What mtask is

A spreadsheet-backed task manager CLI (one project = one Google Spreadsheet),
installable/runnable via `uvx`, with LLM-friendly help and `--json` everywhere.
Backend is gspread; the design rule is **the sheet stores inputs, the CLI
computes views** (don't persist derived values).

## Layout

```
mtask/
  cli.py      # typer app: commands, flags, validation, output
  config.py   # ~/.config/mtask/config.toml (MTASK_CONFIG_DIR override)
  sheet.py    # gspread client + OAuth flow + TaskSheet (all sheet I/O)
README.md     # user docs
HANDOFF.md    # this file
pyproject.toml / uv.lock  # packaging (hatchling), entry point mtask = mtask.cli:app
```

## Schema (sheet columns)

`HEADERS` is derived from `FIELDS` order in `sheet.py` (single source of truth —
change column order there only):

```
ID 親ID 起票日 状態 タイトル 概要 起票者 作業者 先行タスク 状況
開始予定日 完了予定日 開始日 完了日 更新日
```

- Field key ↔ header map: `FIELDS` (English key used by flags/JSON).
- `INPUT_ALIASES` accepts either the English key OR the Japanese header on input.
- Auto-managed: `ID` (`T-0001`…), `起票日`, `更新日`. `状態` enum = `STATUSES`.
- Dates: `YYYY-MM-DD` or empty. Planned = 開始予定日/完了予定日; actual = 開始日/完了日.
- Relationships (text, NOT yet validated against real IDs): `親ID` (parent,
  WBS), `先行タスク` (predecessor IDs, comma-separated).
- `状況` capped at 50,000 chars (Sheets per-cell limit), `--note-max` override.

## Commands

- `add` — single (positional title) or bulk (`--from <json>`, `-`=stdin). All
  WBS/schedule flags supported. Validates BEFORE opening the sheet.
- `update` — three modes: single (`T-0001 --field …`), bulk (`--from`), filter
  (`--where k=v … --set k=v …`). Filter is **dry-run unless `--yes`**.
- `list` — hides 完了/キャンセル by default; `--show-completed/--status/--limit/--page`.
- `get` — one task. `sheet add|list|use|repair`. `user [set]`. `auth login|logout|method|port|path`.
- `sheet repair` — reconcile an existing sheet's columns to the schema
  (read→remap-by-name→rewrite once). Dry-run by default; `--yes` applies and
  copies a `backup_<name>_<ts>` tab first (`--no-backup` to skip). Opens with
  `ensure_header=False`. Caveat: rewrite keeps values, drops formatting/formulas.

## Conventions (keep these consistent)

- **Validation is shared**: `_prepare_add` (add) and `_changes_from_fields`
  (update) are the single validation path for single + bulk + filter. Reuse them
  for any new field. `_validate_date(value, label)`, `_validate_status`,
  `_truncate_note` are the primitives.
- **Validate before network**: error on bad input before calling `_open` so the
  user sees the real error (not an auth/credentials error).
- **Destructive ops** = dry-run by default + explicit `--yes` (+ backup where it
  rewrites). Mirrors `update --where/--set` and `sheet repair`.
- **Batch the Sheets API**: bulk writes use `append_rows` / `update_cells`
  (one request) — see `add_many` / `update_many`. Avoid per-row loops.
- `_build_task` fills every column from an English-key field dict; `add` delegates
  to `add_many`.

## Auth (default = OAuth user flow)

Browser flow via a temporary local WSGI server (`_local_server_flow` in
sheet.py), redirect at `http://localhost:<port><path>` (both configurable).
`authorization_url(access_type="offline", prompt="consent")` is required so a
**refresh_token** is returned; `build_client` self-heals a malformed cached
token. Token at `~/.config/mtask/authorized_user.json`. Service-account is the
headless alternative (`GOOGLE_APPLICATION_CREDENTIALS` always wins).

## Security

Credentials NEVER committed — `.gitignore` excludes oauth_client.json,
authorized_user.json, service_account.json, client_secret*.json, *-key.json,
*.pem, *.p12, .env* etc. They live in `~/.config/mtask/` (outside the repo).
Verify nothing secret is staged before each push. Remote:
`git@github.com:tmlksu/mtask.git` (branch `main`, push as user `tmlksu`).

## Next up — WBS / tree & scheduling views

Columns exist; the relationship-aware **views/validation** are deferred and are
the next milestone. Planned (compute at read time, don't store):

1. `list --tree` — indent children under `親ID`; derive a WBS number (1.2.3) from
   the parent tree on display. Decide ordering (by ID vs WBS) and cycle/orphan
   handling (a parent ID that doesn't exist).
2. `schedule check` — report (don't auto-fix): dependency cycles, 先行タスク not
   yet 完了 while a task is 着手中, a task's 開始予定日 before a predecessor's
   完了予定日, missing/dangling parent or predecessor IDs.
3. Optional conveniences: auto-set 完了日 when 状態→完了 (and 開始日 on →着手中);
   progress rollup of children to a parent.

Design rules to honor: store raw inputs only; relationships are ID references;
emit reports rather than mutating dates; keep it spreadsheet-reasonable (no
auto-rescheduling / Gantt in core).
