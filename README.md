# mtask

Simple spreadsheet-backed task manager CLI, built on [gspread](https://github.com/burnash/gspread).
One project = one Google Spreadsheet. Designed to be friendly for both humans and LLM callers.

## Install / run

### From GitHub (no clone needed)

```bash
# one-off run
uvx --from git+https://github.com/tmlksu/mtask mtask --help

# pin a branch / tag / commit
uvx --from git+https://github.com/tmlksu/mtask@main   mtask list
uvx --from git+https://github.com/tmlksu/mtask@v0.1.0 mtask list

# install as a persistent tool, then just `mtask ...`
uv tool install git+https://github.com/tmlksu/mtask
mtask --help
uv tool upgrade mtask        # update later
```

Notes:
- `--from` is the *package source*; the trailing `mtask` is the *command* to run.
  Plain `uvx mtask` would look up `mtask` on PyPI instead — always pass `--from git+...`.
- Private repo? Use SSH: `git+ssh://git@github.com/tmlksu/mtask`.
- Following a branch? `uvx --refresh ...` (or `uv tool upgrade mtask`) re-fetches.

### From a local checkout

```bash
uvx --from . mtask --help    # one-off
uv tool install .            # install
mtask --help
```

## Auth

mtask supports two methods. **OAuth user login is the default.**

### OAuth (default) — browser flow

1. In Google Cloud, enable the Google Sheets + Drive APIs and create an
   **OAuth client ID of type "Desktop app"**. Download its JSON.
2. Save it to `~/.config/mtask/oauth_client.json`.
3. `mtask auth login` — opens a browser and starts a temporary local HTTP
   server to catch the redirect at `http://localhost:<port>/`. The token is
   cached at `~/.config/mtask/authorized_user.json`.
   (`mtask auth logout` removes it.)

Because you log in as yourself, any spreadsheet you can already edit just works
— no extra sharing needed.

#### Local-server port & path

The redirect endpoint is `http://localhost:<port><path>`. Defaults: port **0**
(an OS-chosen ephemeral port) and path **`/`**. A Desktop-app OAuth client
accepts any `localhost` loopback port/path, so nothing needs registering.

Pin them — e.g. behind a firewall, or to match a redirect URI registered on a
"Web application" client:

```bash
mtask auth port 8765                  # persist a fixed port (0 = ephemeral)
mtask auth path /oauth2callback       # persist a fixed redirect path ('/' default)
mtask auth port                       # show current port
mtask auth path                       # show current path

# override just for one login (not persisted):
mtask auth login --port 8765 --path /oauth2callback
```

### Service account (headless / servers)

1. Create a service account, download its JSON key, enable Sheets + Drive APIs.
2. Point mtask at the key, either:
   - `export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json` (always wins), or
   - place it at `~/.config/mtask/service_account.json` and run
     `mtask auth method service_account`.
3. **Share the spreadsheet** with the service account's email (as Editor).

Check or switch the method anytime: `mtask auth method` / `mtask auth method oauth`.

## Usage

```bash
mtask sheet add myproj <spreadsheet-id>   # register a project (slug -> sheet ID)
mtask sheet list                          # list projects ( * = current )
mtask sheet use myproj                    # switch current project
mtask sheet repair                        # reconcile columns to the schema (dry-run)
mtask sheet view                          # (re)build a collapsible WBS view tab

mtask user set alice                      # default reporter (起票者), git-style
mtask user                                # show current user

mtask add "fix login bug" --status 着手中 --assignee bob
mtask list                                # open tasks (完了 hidden)
mtask list --tree                         # WBS hierarchy by 親ID (numbers 1.2.3)
mtask list --show-completed --limit 100 --page 1
mtask list --status 保留 --json
mtask get T-0001
mtask update T-0001 --status 完了 --note "merged"

# WBS / scheduling
mtask add "design API" --plan-start 2026-07-01 --due 2026-07-10
mtask add "impl endpoint" --parent T-0001 --deps T-0001 --plan-start 2026-07-11
mtask update T-0002 --start 2026-07-11 --status 着手中
```

### Bulk add / update

Pass a JSON array via `--from` (`-` reads stdin). One Sheets request is used per
call (`append_rows` / batch cell update), so it's also gentler on API limits.
Object keys are the same field names as the flags (`title`, `status`, `due`,
`assignee`, `reporter`, `note`; plus `id` for updates) — Japanese headers like
`タイトル` also work.

```bash
# bulk add — IDs are auto-assigned
mtask add --from tasks.json
echo '[{"title":"task A"},{"title":"task B","status":"着手中"}]' | mtask add --from -

# bulk update — each object needs an "id"
mtask update --from updates.json
echo '[{"id":"T-0001","status":"完了"},{"id":"T-0002","assignee":"alice"}]' \
  | mtask update --from -
```

### WBS tree view (`list --tree`)

`mtask list --tree` renders the `親ID` hierarchy with derived WBS numbers
(`1`, `1.1`, `1.2.1`, …), indented by depth. Siblings are ordered by ID.

```
1       T-0001  [着手中]  設計
1.1       T-0002  [未着手]  API定義
1.2       T-0003  [完了]    スキーマ
2       T-0004  [未着手]  実装
```

- Numbers are computed from the full tree, so a task's number is stable
  regardless of filtering (a hidden sibling just leaves a gap, e.g. `1.1` then `1.3`).
- Ancestors of a matching task are always shown (dimmed) for context — so
  filtering (e.g. the default hide-完了) never breaks the hierarchy.
- `--tree` shows the whole tree and ignores `--limit`/`--page`.
- Anomalies are flagged inline: `(親?)` = `親ID` points to a missing/own ID
  (shown as a root), `(循環)` = part of a parent cycle (the loop is broken).
- `--json --tree` adds `wbs`, `depth`, `context`, and `flags` to each row.

### In-sheet WBS view (`sheet view`)

For people browsing the spreadsheet itself, `mtask sheet view` generates a
formatted, **collapsible** WBS into a separate tab (default name `WBS`):

```bash
mtask sheet view              # build/refresh the 'WBS' tab
mtask sheet view --name 計画   # use a different tab name
```

- WBS numbers + indentation, **native row groups** (the +/- outline) so the
  hierarchy collapses, rows **color-coded by 状態**, parent rows bold, header
  frozen.
- The tab is rebuilt from scratch each run (no stale formatting/groups) and is
  protected (warning-only). **The data sheet is never modified.**
- It's a snapshot — re-run after changes. (No Apps Script needed; it's all done
  through the Sheets API with your existing login.)

### Filter update (`--where` / `--set`)

Update every task matching a condition. **It's a dry-run by default** — it prints
what would change and applies nothing until you add `--yes`.

```bash
mtask update --where assignee=bob --set status=完了        # preview only
mtask update --where assignee=bob --set status=完了 --yes  # apply
mtask update --where 状態=着手中 --set assignee=alice --yes  # repeatable --where (AND)
```

## Sheet columns

`ID` ・ `親ID` ・ `起票日` ・ `状態` ・ `タイトル` ・ `概要` ・ `起票者` ・ `作業者` ・
`先行タスク` ・ `状況` ・ `開始予定日` ・ `完了予定日` ・ `開始日` ・ `完了日` ・ `更新日`

- `ID` is auto-assigned as `T-0001`, `T-0002`, …
- `起票日` / `更新日` are managed automatically.
- `状態` is restricted to: **未着手 / 着手中 / 完了 / 保留 / キャンセル**.
- `状況` is truncated to 50,000 chars by default — the Google Sheets per-cell
  limit — with a warning; override with `--note-max`.
- `list` hides **完了** and **キャンセル** by default; use `--show-completed`
  (or `--status` to target a specific state).

### WBS / scheduling fields

These let you grow a flat task list into a schedule. The sheet stores the raw
values; computed views (tree display, dependency/schedule checks) are planned as
a follow-up.

| Field | Flag | Notes |
|-------|------|-------|
| `親ID` | `--parent` | Parent task ID for a subtask (WBS hierarchy). Empty = top-level. |
| `先行タスク` | `--deps` | Predecessor task IDs, comma-separated (e.g. `T-0003,T-0005`). |
| `概要` | `--summary` | Short description (distinct from `状況`, the running notes). |
| `開始予定日` | `--plan-start` | Planned start. `YYYY-MM-DD` or empty. |
| `完了予定日` | `--due` / `-d` | Planned finish (deadline). `YYYY-MM-DD` or empty. |
| `開始日` | `--start` | Actual start. `YYYY-MM-DD` or empty. |
| `完了日` | `--finish` | Actual finish. `YYYY-MM-DD` or empty. |

All date fields accept `''` to clear them on `update`. `親ID` / `先行タスク` are
stored as plain text for now — they aren't validated against existing IDs yet.

### Repairing an existing sheet's columns

The header is fixed and checked on every run, so an existing populated sheet
whose columns differ (e.g. from before these fields were added) reports a header
mismatch. `mtask sheet repair` reconciles it:

```bash
mtask sheet repair            # dry-run: show what would change
mtask sheet repair --yes      # apply (a backup tab is created first)
mtask sheet repair --yes --no-backup
```

It matches columns **by header name**, reorders them to the schema order, adds
any missing columns (empty), and keeps unknown columns on the right so no data
is dropped. It reads the sheet once and rewrites it in one pass; cell formatting
and formulas are **not** preserved (values are). By default it first copies the
sheet to a `backup_<name>_<timestamp>` tab.

## Config

`~/.config/mtask/config.toml` (override dir with `MTASK_CONFIG_DIR`):

```toml
[user]
name = "alice"

[current]
sheet = "myproj"

[sheets]
myproj = "<spreadsheet-id>"
```
