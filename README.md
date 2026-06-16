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

mtask user set alice                      # default reporter (起票者), git-style
mtask user                                # show current user

mtask add "fix login bug" --status 着手中 --assignee bob
mtask list                                # open tasks (完了 hidden)
mtask list --show-completed --limit 100 --page 1
mtask list --status 保留 --json
mtask get T-0001
mtask update T-0001 --status 完了 --note "merged"
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

### Filter update (`--where` / `--set`)

Update every task matching a condition. **It's a dry-run by default** — it prints
what would change and applies nothing until you add `--yes`.

```bash
mtask update --where assignee=bob --set status=完了        # preview only
mtask update --where assignee=bob --set status=完了 --yes  # apply
mtask update --where 状態=着手中 --set assignee=alice --yes  # repeatable --where (AND)
```

## Sheet columns

`ID` ・ `起票日` ・ `状態` ・ `タイトル` ・ `起票者` ・ `作業者` ・ `状況` ・ `完了予定日` ・ `更新日`

- `ID` is auto-assigned as `T-0001`, `T-0002`, …
- `起票日` / `更新日` are managed automatically.
- `状態` is restricted to: **未着手 / 着手中 / 完了 / 保留 / キャンセル**.
- `完了予定日` (due date) must be `YYYY-MM-DD` (or empty); pass `--due ''` to clear it.
- `list` hides **完了** and **キャンセル** by default; use `--show-completed`
  (or `--status` to target a specific state).
- `状況` is truncated to 50,000 chars by default — the Google Sheets per-cell
  limit — with a warning; override with `--note-max`.

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
