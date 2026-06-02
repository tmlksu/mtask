"""mtask — spreadsheet-backed task manager CLI.

WORKFLOW (for humans and LLMs):

  1. mtask auth login                            # OAuth browser flow (default)
  2. mtask sheet add myproj <spreadsheet-id>     # register a project (one sheet)
  3. mtask user set alice                        # set default reporter (起票者)
  4. mtask add "fix login bug" --status 着手中    # create a task -> prints its ID
  5. mtask list                                  # open tasks (完了/キャンセル hidden)
  6. mtask update T-0001 --status 完了            # change fields by ID

Notes for LLM callers:
  * All commands accept --json for machine-readable output.
  * `--status` only accepts: 未着手 / 着手中 / 完了 / 保留 / キャンセル.
    Any other value errors out and lists the allowed values.
  * `update` changes ONLY the fields you pass; omit a flag to leave it unchanged.
  * 起票日 / 更新日 / ID are managed automatically.
"""

from __future__ import annotations

import datetime as dt
import json
from enum import Enum
from typing import Optional

import typer

from . import config
from .sheet import (
    HIDDEN_BY_DEFAULT,
    NOTE_MAX_DEFAULT,
    SheetError,
    TaskSheet,
    build_client,
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=__doc__,
    rich_markup_mode=None,
)
sheet_app = typer.Typer(no_args_is_help=True, help="Manage projects (one project = one spreadsheet).")
app.add_typer(sheet_app, name="sheet")


class Status(str, Enum):
    not_started = "未着手"
    in_progress = "着手中"
    done = "完了"
    on_hold = "保留"
    cancelled = "キャンセル"


SHEET_OPT = typer.Option(None, "--sheet", "-s", help="Project slug to operate on (default: current).")


def _err(msg: str) -> None:
    typer.secho(f"Error: {msg}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _open(slug: Optional[str]) -> TaskSheet:
    try:
        _, sid = config.resolve_spreadsheet_id(slug)
        return TaskSheet(sid)
    except (LookupError, SheetError) as e:
        _err(str(e))
        raise  # unreachable


def _validate_due(due: str) -> str:
    """Accept an empty string or a YYYY-MM-DD date; error otherwise."""
    if due == "":
        return ""
    try:
        return dt.date.fromisoformat(due).isoformat()
    except ValueError:
        _err(f"invalid due date '{due}'; expected YYYY-MM-DD (e.g. 2026-06-30) or empty")
        raise  # unreachable


def _truncate_note(note: str, limit: int) -> str:
    if len(note) > limit:
        typer.secho(
            f"Warning: note truncated to {limit} chars (was {len(note)})",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return note[:limit]
    return note


# --- task commands ---------------------------------------------------------

@app.command()
def add(
    title: str = typer.Argument(..., help="Task title (必須)."),
    status: Status = typer.Option(Status.not_started, "--status", help="状態."),
    due: str = typer.Option("", "--due", "-d", help="完了予定日 (YYYY-MM-DD). Empty allowed."),
    assignee: str = typer.Option("", "--assignee", "-a", help="作業者 (default: empty)."),
    reporter: Optional[str] = typer.Option(None, "--reporter", help="起票者 (default: configured user)."),
    note: str = typer.Option("", "--note", "-n", help="状況 / free-text notes."),
    note_max: int = typer.Option(NOTE_MAX_DEFAULT, "--note-max", help="Max chars for 状況 (overflow is cut)."),
    sheet: Optional[str] = SHEET_OPT,
    json_out: bool = typer.Option(False, "--json", help="Print created task as JSON."),
):
    """Add a task. Prints the new task ID."""
    rep = reporter or config.get_user() or ""
    due_v = _validate_due(due)
    note_v = _truncate_note(note, note_max)
    ts = _open(sheet)
    try:
        task = ts.add(
            title=title,
            status=status.value,
            due=due_v,
            reporter=rep,
            assignee=assignee,
            note=note_v,
        )
    except SheetError as e:
        _err(str(e))
    if json_out:
        typer.echo(json.dumps(task, ensure_ascii=False))
    else:
        typer.secho(f"added {task['ID']}: {task['タイトル']}", fg=typer.colors.GREEN)


@app.command()
def update(
    task_id: str = typer.Argument(..., help="Task ID, e.g. T-0001."),
    status: Optional[Status] = typer.Option(None, "--status", help="状態."),
    due: Optional[str] = typer.Option(None, "--due", "-d", help="完了予定日 (YYYY-MM-DD, or '' to clear)."),
    assignee: Optional[str] = typer.Option(None, "--assignee", "-a", help="作業者."),
    reporter: Optional[str] = typer.Option(None, "--reporter", help="起票者."),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="タイトル."),
    note: Optional[str] = typer.Option(None, "--note", "-n", help="状況."),
    note_max: int = typer.Option(NOTE_MAX_DEFAULT, "--note-max", help="Max chars for 状況."),
    sheet: Optional[str] = SHEET_OPT,
    json_out: bool = typer.Option(False, "--json", help="Print updated task as JSON."),
):
    """Update a task's fields by ID. Only the fields you pass are changed."""
    changes: dict[str, str] = {}
    if status is not None:
        changes["状態"] = status.value
    if due is not None:
        changes["完了予定日"] = _validate_due(due)
    if assignee is not None:
        changes["作業者"] = assignee
    if reporter is not None:
        changes["起票者"] = reporter
    if title is not None:
        changes["タイトル"] = title
    if note is not None:
        changes["状況"] = _truncate_note(note, note_max)
    if not changes:
        _err(
            "no fields to update; pass at least one of "
            "--status/--due/--assignee/--reporter/--title/--note"
        )

    ts = _open(sheet)
    try:
        task = ts.update(task_id, changes)
    except SheetError as e:
        _err(str(e))
    if json_out:
        typer.echo(json.dumps(task, ensure_ascii=False))
    else:
        typer.secho(f"updated {task_id}: {', '.join(changes)}", fg=typer.colors.GREEN)


@app.command(name="list")
def list_(
    status: Optional[Status] = typer.Option(None, "--status", help="Filter to a single 状態."),
    show_completed: bool = typer.Option(False, "--show-completed", help="Include 完了 tasks (hidden by default)."),
    limit: int = typer.Option(50, "--limit", "-l", help="Max rows per page."),
    page: int = typer.Option(0, "--page", "-p", help="Page number, 0-indexed."),
    sheet: Optional[str] = SHEET_OPT,
    json_out: bool = typer.Option(False, "--json", help="Print rows as a JSON array."),
):
    """List tasks. By default 完了 is hidden; use --show-completed to include it."""
    ts = _open(sheet)
    try:
        rows = ts.records()
    except SheetError as e:
        _err(str(e))

    if status is not None:
        rows = [r for r in rows if r.get("状態") == status.value]
    elif not show_completed:
        rows = [r for r in rows if r.get("状態") not in HIDDEN_BY_DEFAULT]

    total = len(rows)
    start = page * limit
    page_rows = rows[start : start + limit]

    if json_out:
        typer.echo(json.dumps(page_rows, ensure_ascii=False))
        return

    if not page_rows:
        typer.secho("(no tasks)", fg=typer.colors.BRIGHT_BLACK)
        return
    for r in page_rows:
        line = f"{r['ID']}  [{r['状態']}]  {r['タイトル']}"
        if r.get("作業者"):
            line += f"  @{r['作業者']}"
        if r.get("完了予定日"):
            line += f"  〆{r['完了予定日']}"
        typer.echo(line)
        typer.secho(
            f"        起票:{r.get('起票日', '')}  更新:{r.get('更新日', '')}",
            fg=typer.colors.BRIGHT_BLACK,
        )
    shown = min(start + limit, total)
    typer.secho(f"-- {start + 1}-{shown} of {total} (page {page}) --", fg=typer.colors.BRIGHT_BLACK)


@app.command()
def get(
    task_id: str = typer.Argument(..., help="Task ID, e.g. T-0001."),
    sheet: Optional[str] = SHEET_OPT,
    json_out: bool = typer.Option(False, "--json", help="Print task as JSON."),
):
    """Show a single task by ID."""
    ts = _open(sheet)
    try:
        task = ts.get(task_id)
    except SheetError as e:
        _err(str(e))
    if json_out:
        typer.echo(json.dumps(task, ensure_ascii=False))
    else:
        for k, v in task.items():
            typer.echo(f"{k}: {v}")


# --- user command ----------------------------------------------------------

user_app = typer.Typer(
    invoke_without_command=True,
    help="Get or set the default reporter (起票者), git-style.",
)
app.add_typer(user_app, name="user")


@user_app.callback()
def user_main(ctx: typer.Context):
    """Show the current default reporter when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        typer.echo(config.get_user() or "(unset)")


@user_app.command("set")
def user_set(name: str = typer.Argument(..., help="Default 起票者 name.")):
    """Set the default reporter (起票者)."""
    config.set_user(name)
    typer.secho(f"user set to {name}", fg=typer.colors.GREEN)


# --- auth commands ---------------------------------------------------------

auth_app = typer.Typer(no_args_is_help=True, help="Authentication (default: OAuth browser flow).")
app.add_typer(auth_app, name="auth")


class AuthMethod(str, Enum):
    oauth = "oauth"
    service_account = "service_account"


@auth_app.command("login")
def auth_login(
    port: Optional[int] = typer.Option(
        None, "--port", help="Local-server port for this login (default: configured port, 0 = ephemeral)."
    ),
    path: Optional[str] = typer.Option(
        None, "--path", help="Redirect endpoint path for this login (default: configured path, '/')."
    ),
):
    """Run the OAuth browser flow now and cache the token.

    Opens a browser and starts a temporary local HTTP server to catch the
    redirect at http://localhost:<port><path>. Requires an OAuth client secret
    at ~/.config/mtask/oauth_client.json (Desktop-app type).
    """
    try:
        # triggers the local-server flow if not yet authorized
        build_client(oauth_port=port, oauth_path=path)
    except (SheetError, ValueError) as e:
        _err(str(e))
    typer.secho("authenticated; token cached", fg=typer.colors.GREEN)


@auth_app.command("logout")
def auth_logout():
    """Delete the cached OAuth token."""
    path = config.authorized_user_path()
    if path.exists():
        path.unlink()
        typer.secho("logged out (token removed)", fg=typer.colors.GREEN)
    else:
        typer.secho("not logged in", fg=typer.colors.BRIGHT_BLACK)


@auth_app.command("method")
def auth_method(
    method: Optional[AuthMethod] = typer.Argument(None, help="Set auth method. Omit to show current."),
):
    """Get or set the auth method: oauth (default) or service_account."""
    if method is None:
        typer.echo(config.get_auth_method())
    else:
        config.set_auth_method(method.value)
        typer.secho(f"auth method -> {method.value}", fg=typer.colors.GREEN)


@auth_app.command("port")
def auth_port(
    port: Optional[int] = typer.Argument(None, help="Set OAuth local-server port. Omit to show current."),
):
    """Get or set the OAuth local-server port (0 = OS-chosen ephemeral port)."""
    if port is None:
        typer.echo(config.get_auth_port())
    else:
        try:
            config.set_auth_port(port)
        except ValueError as e:
            _err(str(e))
        typer.secho(f"auth port -> {port}", fg=typer.colors.GREEN)


@auth_app.command("path")
def auth_path(
    path: Optional[str] = typer.Argument(
        None, help="Set OAuth redirect endpoint path, e.g. /oauth2callback. Omit to show current."
    ),
):
    """Get or set the OAuth redirect endpoint path (default '/')."""
    if path is None:
        typer.echo(config.get_auth_path())
    else:
        try:
            config.set_auth_path(path)
        except ValueError as e:
            _err(str(e))
        typer.secho(f"auth path -> {config.get_auth_path()}", fg=typer.colors.GREEN)


# --- sheet (project) commands ---------------------------------------------

@sheet_app.command("add")
def sheet_add(
    slug: str = typer.Argument(..., help="Project slug, e.g. myproj."),
    spreadsheet_id: str = typer.Argument(..., help="Google Spreadsheet ID (from its URL)."),
):
    """Register a project: map a slug to a spreadsheet ID."""
    config.add_sheet(slug, spreadsheet_id)
    typer.secho(f"registered '{slug}' -> {spreadsheet_id}", fg=typer.colors.GREEN)


@sheet_app.command("list")
def sheet_list():
    """List registered projects."""
    sheets = config.get_sheets()
    current = config.current_sheet()
    if not sheets:
        typer.secho("(no sheets registered)", fg=typer.colors.BRIGHT_BLACK)
        return
    for slug, sid in sheets.items():
        marker = "*" if slug == current else " "
        typer.echo(f"{marker} {slug}\t{sid}")


@sheet_app.command("use")
def sheet_use(slug: str = typer.Argument(..., help="Project slug to select as current.")):
    """Select the current project."""
    try:
        config.use_sheet(slug)
    except KeyError:
        _err(f"unknown sheet '{slug}'; register it first with `mtask sheet add`")
    typer.secho(f"current sheet -> {slug}", fg=typer.colors.GREEN)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
