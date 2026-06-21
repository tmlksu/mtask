# HANDOFF — mtask

Working notes for continuing development. User-facing usage lives in `README.md`;
this file captures architecture, conventions, and what's next.

## Docs map

| Doc | Role | Update when |
|-----|------|-------------|
| `README.md` | How to use it | behavior/flags change |
| `HANDOFF.md` | How to work on it now (this file) | architecture/conventions/roadmap shift |
| `docs/adr/` | **Why** it's built this way (append-only) | a decision with real trade-offs is made — add `NNNN-*.md` |
| `CHANGELOG.md` | **What** changed when (Keep a Changelog) | any notable user-facing change — add under `[Unreleased]` |

Don't rewrite an accepted ADR to reverse it — add a new ADR that supersedes it.
Routine changes go in commits + CHANGELOG, not ADRs.

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
CHANGELOG.md  # notable changes (Keep a Changelog)
docs/adr/     # architecture decision records (why)
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

## WBS / tree & scheduling views

Compute at read time, don't store.

**Done:**
- `list --tree` — WBS hierarchy from `親ID`, derived numbers (1.2.3), indent by
  depth, siblings by ID. Numbers from the full tree (stable under filtering);
  ancestors of matches shown dimmed as context; orphan `(親?)` / cycle `(循環)`
  flagged and made roots so traversal terminates. JSON adds wbs/depth/context/
  flags. Pure logic in `wbs_tree` (sheet.py), rendered by `_render_tree` (cli.py).
- `sheet view` — human-friendly in-sheet WBS. Generates a separate tab (default
  `WBS`) via the Sheets API (no GAS): WBS numbers + indentation, native
  collapsible row groups (`addDimensionGroup` over each node's descendant rows),
  color by 状態 (conditional formatting), bold parents, frozen header, protected
  (warning-only). Tab is deleted+recreated each run so formatting/groups never
  accumulate; data sheet never touched. Logic in `TaskSheet.build_view`. Decided
  against GAS (extra scopes, clasp/API deploy, dual codebase) since the native
  collapsible outline is reachable from Python; revisit only if live auto-refresh
  on manual edits becomes a primary need.

- `schedule check` — report-only diagnostics (ADR-0006). Pure `schedule_findings`
  (sheet.py): parent cycle/dangling/self (reuses wbs_tree flags), predecessor
  cycle/dangling/self (`_nodes_in_cycles`), inverted plan/actual dates (errors);
  predecessor-not-done and starts-before-predecessor-due (warnings). CLI
  `schedule check` exits 1 on errors. Dates compared as ISO strings.

**Next up:**
1. 簡易ガント — surface (in-sheet view extension vs terminal ASCII) TBD; v1 draws
   bars from 開始予定日/完了予定日 (fallback actual), WBS order, today marker.
2. Optional conveniences: auto-set 完了日 when 状態→完了 (and 開始日 on →着手中);
   progress rollup of children to a parent.

Design rules to honor: store raw inputs only; relationships are ID references;
emit reports rather than mutating dates; keep it spreadsheet-reasonable (no
auto-rescheduling / Gantt in core).
