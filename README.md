# mtask

Simple spreadsheet-backed task manager CLI, built on [gspread](https://github.com/burnash/gspread).
One project = one Google Spreadsheet. Designed to be friendly for both humans and LLM callers.

## Install / run

```bash
# one-off
uvx --from . mtask --help

# install as a tool
uv tool install .
mtask --help
```

## Auth

mtask supports two methods. **OAuth user login is the default.**

### OAuth (default) — browser flow

1. In Google Cloud, enable the Google Sheets + Drive APIs and create an
   **OAuth client ID of type "Desktop app"**. Download its JSON.
2. Save it to `~/.config/mtask/oauth_client.json`.
3. `mtask auth login` — opens a browser and starts a temporary local HTTP
   server to catch the redirect. The token is cached at
   `~/.config/mtask/authorized_user.json`.
   (`mtask auth logout` removes it.)

Because you log in as yourself, any spreadsheet you can already edit just works
— no extra sharing needed.

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

## Sheet columns

`ID` ・ `起票日` ・ `更新日` ・ `状態` ・ `完了予定日` ・ `起票者` ・ `作業者` ・ `タイトル` ・ `状況`

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
