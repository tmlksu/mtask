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
  * Bulk: `add --from <json>` / `update --from <json>` take a JSON array
    ('-' = stdin); `update --where k=v --set k=v` updates all matches
    (dry-run unless --yes). Object keys match the flag names (id/title/status/…).
  * 起票日 / 更新日 / ID are managed automatically.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from enum import Enum
from typing import Optional

import typer

from . import config
from .sheet import (
    FIELDS,
    HIDDEN_BY_DEFAULT,
    INPUT_ALIASES,
    NOTE_MAX_DEFAULT,
    STATUSES,
    SheetError,
    TaskSheet,
    build_client,
    wbs_tree,
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


def _open(slug: Optional[str], *, ensure_header: bool = True) -> TaskSheet:
    try:
        _, sid = config.resolve_spreadsheet_id(slug)
        return TaskSheet(sid, ensure_header=ensure_header)
    except (LookupError, SheetError) as e:
        _err(str(e))
        raise  # unreachable


def _validate_date(value: str, label: str = "date") -> str:
    """Accept an empty string or a YYYY-MM-DD date; error otherwise."""
    if value == "":
        return ""
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        _err(f"invalid {label} '{value}'; expected YYYY-MM-DD (e.g. 2026-06-30) or empty")
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


def _validate_status(value: str) -> str:
    if value not in STATUSES:
        _err(f"invalid 状態 '{value}'; allowed: {' / '.join(STATUSES)}")
    return value


# --- bulk / filter helpers -------------------------------------------------

def _s(v) -> str:
    """JSON value -> string; null becomes empty."""
    return "" if v is None else str(v)


def _read_json_array(src: str) -> list:
    """Read a JSON array from a file path, or from stdin when src == '-'."""
    try:
        text = sys.stdin.read() if src == "-" else open(src, encoding="utf-8").read()
    except OSError as e:
        _err(f"cannot read --from {src!r}: {e}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        _err(f"--from is not valid JSON: {e}")
    if not isinstance(data, list):
        _err("--from must be a JSON array of objects, e.g. [{\"title\": \"...\"}]")
    return data


def _normalize_keys(raw: dict, idx: int, *, allow_id: bool) -> dict[str, str]:
    """Map an input object's keys to English field keys, rejecting bad ones."""
    if not isinstance(raw, dict):
        _err(f"item {idx}: must be a JSON object")
    out: dict[str, str] = {}
    for k, v in raw.items():
        key = INPUT_ALIASES.get(k)
        if key is None:
            _err(f"item {idx}: unknown field '{k}'; allowed: {', '.join(sorted(set(INPUT_ALIASES) ))}")
        if key in ("created", "updated"):
            _err(f"item {idx}: '{k}' is managed automatically and can't be set")
        if key == "id" and not allow_id:
            _err(f"item {idx}: 'id' can't be set on add (auto-assigned)")
        out[key] = _s(v)
    return out


# English field key -> Japanese header, for plain (unvalidated) string fields.
_PLAIN_FIELDS = {
    "parent": "親ID",
    "title": "タイトル",
    "summary": "概要",
    "reporter": "起票者",
    "assignee": "作業者",
    "deps": "先行タスク",
}
# English field key -> (header, label) for date fields validated as YYYY-MM-DD.
_DATE_FIELDS = {
    "plan_start": ("開始予定日", "開始予定日"),
    "due": ("完了予定日", "完了予定日"),
    "start": ("開始日", "開始日"),
    "finish": ("完了日", "完了日"),
}


def _changes_from_fields(fields: dict[str, str], note_max: int) -> dict[str, str]:
    """English field dict -> validated {Japanese header: value} changes."""
    changes: dict[str, str] = {}
    if "status" in fields:
        changes["状態"] = _validate_status(fields["status"])
    if "note" in fields:
        changes["状況"] = _truncate_note(fields["note"], note_max)
    for key, header in _PLAIN_FIELDS.items():
        if key in fields:
            changes[header] = fields[key]
    for key, (header, label) in _DATE_FIELDS.items():
        if key in fields:
            changes[header] = _validate_date(fields[key], label)
    return changes


def _prepare_add(f: dict[str, str], note_max: int, default_reporter: str) -> dict[str, str]:
    """Validate an English-key field dict for `add`; fills defaults."""
    if not f.get("title"):
        _err("'title' is required")
    out = {
        "title": f["title"],
        "status": _validate_status(f.get("status", Status.not_started.value)),
        "note": _truncate_note(f.get("note", ""), note_max),
        "reporter": f.get("reporter", default_reporter),
    }
    for key, header in _PLAIN_FIELDS.items():
        if key in ("title", "reporter"):
            continue
        out[key] = f.get(key, "")
    for key, (header, label) in _DATE_FIELDS.items():
        out[key] = _validate_date(f.get(key, ""), label)
    return out


def _parse_pairs(pairs: Optional[list[str]]) -> dict[str, str]:
    """Parse repeated 'key=value' options into an English field dict."""
    out: dict[str, str] = {}
    for p in pairs or []:
        if "=" not in p:
            _err(f"expected key=value, got '{p}'")
        k, v = p.split("=", 1)
        key = INPUT_ALIASES.get(k.strip())
        if key is None:
            _err(f"unknown field '{k.strip()}'; allowed: {', '.join(sorted(set(INPUT_ALIASES)))}")
        out[key] = v
    return out


# --- task commands ---------------------------------------------------------

@app.command()
def add(
    title: Optional[str] = typer.Argument(None, help="Task title (必須 unless --from is used)."),
    status: Status = typer.Option(Status.not_started, "--status", help="状態."),
    summary: str = typer.Option("", "--summary", help="概要 / short description."),
    parent: str = typer.Option("", "--parent", help="親ID for a subtask (WBS), e.g. T-0001."),
    deps: str = typer.Option("", "--deps", help="先行タスク (predecessor IDs, comma-separated)."),
    plan_start: str = typer.Option("", "--plan-start", help="開始予定日 (YYYY-MM-DD)."),
    due: str = typer.Option("", "--due", "-d", help="完了予定日 (YYYY-MM-DD). Empty allowed."),
    start: str = typer.Option("", "--start", help="開始日 / actual start (YYYY-MM-DD)."),
    finish: str = typer.Option("", "--finish", help="完了日 / actual finish (YYYY-MM-DD)."),
    assignee: str = typer.Option("", "--assignee", "-a", help="作業者 (default: empty)."),
    reporter: Optional[str] = typer.Option(None, "--reporter", help="起票者 (default: configured user)."),
    note: str = typer.Option("", "--note", "-n", help="状況 / free-text notes."),
    note_max: int = typer.Option(NOTE_MAX_DEFAULT, "--note-max", help="Max chars for 状況 (overflow is cut)."),
    from_file: Optional[str] = typer.Option(
        None, "--from", help="Bulk add: JSON array of task objects ('-' = stdin). ID is auto-assigned."
    ),
    sheet: Optional[str] = SHEET_OPT,
    json_out: bool = typer.Option(False, "--json", help="Print created task(s) as JSON."),
):
    """Add a task (or many with --from). Prints the new task ID(s).

    Single:  mtask add "fix bug" --status 着手中 --assignee bob
    Bulk:    mtask add --from tasks.json     # [{"title": "...", "status": "..."}, ...]
             echo '[{"title":"a"},{"title":"b"}]' | mtask add --from -
    """
    if from_file is not None:
        if title is not None:
            _err("pass either a title argument or --from, not both")
        _add_bulk(from_file, note_max, sheet, json_out)
        return
    if title is None:
        _err("missing TITLE; give a title argument or use --from for bulk add")

    f = {
        "title": title,
        "status": status.value,
        "summary": summary,
        "parent": parent,
        "deps": deps,
        "plan_start": plan_start,
        "due": due,
        "start": start,
        "finish": finish,
        "assignee": assignee,
        "note": note,
    }
    if reporter is not None:
        f["reporter"] = reporter
    fields = _prepare_add(f, note_max, config.get_user() or "")
    ts = _open(sheet)
    try:
        task = ts.add(fields)
    except SheetError as e:
        _err(str(e))
    if json_out:
        typer.echo(json.dumps(task, ensure_ascii=False))
    else:
        typer.secho(f"added {task['ID']}: {task['タイトル']}", fg=typer.colors.GREEN)


def _add_bulk(from_file: str, note_max: int, sheet: Optional[str], json_out: bool) -> None:
    items = _read_json_array(from_file)
    default_reporter = config.get_user() or ""
    normalized: list[dict[str, str]] = []
    for idx, raw in enumerate(items):
        f = _normalize_keys(raw, idx, allow_id=False)
        if not f.get("title"):
            _err(f"item {idx}: 'title' is required")
        normalized.append(_prepare_add(f, note_max, default_reporter))
    if not normalized:
        _err("--from contained no tasks")
    ts = _open(sheet)
    try:
        tasks = ts.add_many(normalized)
    except SheetError as e:
        _err(str(e))
    if json_out:
        typer.echo(json.dumps(tasks, ensure_ascii=False))
    else:
        ids = [t["ID"] for t in tasks]
        typer.secho(f"added {len(ids)} tasks: {ids[0]}..{ids[-1]}", fg=typer.colors.GREEN)


@app.command()
def update(
    task_id: Optional[str] = typer.Argument(None, help="Task ID, e.g. T-0001 (single-update mode)."),
    status: Optional[Status] = typer.Option(None, "--status", help="状態."),
    summary: Optional[str] = typer.Option(None, "--summary", help="概要 / short description."),
    parent: Optional[str] = typer.Option(None, "--parent", help="親ID (WBS); '' to clear."),
    deps: Optional[str] = typer.Option(None, "--deps", help="先行タスク (comma-separated); '' to clear."),
    plan_start: Optional[str] = typer.Option(None, "--plan-start", help="開始予定日 (YYYY-MM-DD, '' to clear)."),
    due: Optional[str] = typer.Option(None, "--due", "-d", help="完了予定日 (YYYY-MM-DD, or '' to clear)."),
    start: Optional[str] = typer.Option(None, "--start", help="開始日 / actual start (YYYY-MM-DD, '' to clear)."),
    finish: Optional[str] = typer.Option(None, "--finish", help="完了日 / actual finish (YYYY-MM-DD, '' to clear)."),
    assignee: Optional[str] = typer.Option(None, "--assignee", "-a", help="作業者."),
    reporter: Optional[str] = typer.Option(None, "--reporter", help="起票者."),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="タイトル."),
    note: Optional[str] = typer.Option(None, "--note", "-n", help="状況."),
    note_max: int = typer.Option(NOTE_MAX_DEFAULT, "--note-max", help="Max chars for 状況."),
    from_file: Optional[str] = typer.Option(
        None, "--from", help="Bulk update: JSON array of objects, each with 'id' + fields ('-' = stdin)."
    ),
    where: Optional[list[str]] = typer.Option(
        None, "--where", help="Filter mode: 'key=value' condition (repeatable, AND-ed)."
    ),
    set_: Optional[list[str]] = typer.Option(
        None, "--set", help="Filter mode: 'key=value' field to set on every match (repeatable)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Apply a filter update; without it, only a dry-run preview."),
    sheet: Optional[str] = SHEET_OPT,
    json_out: bool = typer.Option(False, "--json", help="Print updated task(s) as JSON."),
):
    """Update tasks by ID, in bulk (--from), or by filter (--where/--set).

    Single:  mtask update T-0001 --status 完了 --note merged
    Bulk:    mtask update --from updates.json   # [{"id":"T-0001","status":"完了"}, ...]
    Filter:  mtask update --where assignee=bob --set status=完了   (add --yes to apply)
    """
    single = {
        "status": status.value if status is not None else None,
        "summary": summary,
        "parent": parent,
        "deps": deps,
        "plan_start": plan_start,
        "due": due,
        "start": start,
        "finish": finish,
        "assignee": assignee,
        "reporter": reporter,
        "title": title,
        "note": note,
    }
    filter_mode = bool(where) or bool(set_)
    single_fields = {k: v for k, v in single.items() if v is not None}

    if from_file is not None:
        if task_id is not None or filter_mode or single_fields:
            _err("--from can't be combined with an ID, single-field flags, or --where/--set")
        _update_bulk(from_file, note_max, sheet, json_out)
        return
    if filter_mode:
        if task_id is not None or single_fields:
            _err("--where/--set can't be combined with an ID or single-field flags")
        _update_filter(where, set_, note_max, yes, sheet, json_out)
        return
    if task_id is None:
        _err("missing ID; pass a task ID, or use --from / --where+--set for bulk updates")

    changes = _changes_from_fields(single_fields, note_max)
    if not changes:
        _err(
            "no fields to update; pass at least one field flag "
            "(--status/--title/--summary/--parent/--deps/--assignee/--reporter/"
            "--note/--plan-start/--due/--start/--finish)"
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


def _update_bulk(from_file: str, note_max: int, sheet: Optional[str], json_out: bool) -> None:
    items = _read_json_array(from_file)
    updates: list[tuple[str, dict[str, str]]] = []
    for idx, raw in enumerate(items):
        f = _normalize_keys(raw, idx, allow_id=True)
        tid = f.pop("id", "")
        if not tid:
            _err(f"item {idx}: 'id' is required for update")
        changes = _changes_from_fields(f, note_max)
        if not changes:
            _err(f"item {idx} ({tid}): no fields to update")
        updates.append((tid, changes))
    if not updates:
        _err("--from contained no updates")
    ts = _open(sheet)
    try:
        applied = ts.update_many(updates)
    except SheetError as e:
        _err(str(e))
    if json_out:
        typer.echo(json.dumps(applied, ensure_ascii=False))
    else:
        typer.secho(f"updated {len(applied)} tasks: {', '.join(applied)}", fg=typer.colors.GREEN)


def _update_filter(
    where: Optional[list[str]],
    set_: Optional[list[str]],
    note_max: int,
    yes: bool,
    sheet: Optional[str],
    json_out: bool,
) -> None:
    conds = {FIELDS[k]: v for k, v in _parse_pairs(where).items()}  # header -> expected value
    changes = _changes_from_fields(_parse_pairs(set_), note_max)
    if not changes:
        _err("filter update needs at least one --set key=value")

    ts = _open(sheet)
    try:
        rows = ts.records()
    except SheetError as e:
        _err(str(e))
    matched = [r for r in rows if all(str(r.get(h, "")) == v for h, v in conds.items())]

    if not matched:
        typer.secho("(no tasks matched)", fg=typer.colors.BRIGHT_BLACK)
        return
    if not yes:
        typer.secho(
            f"dry-run: {len(matched)} task(s) match; would set "
            f"{', '.join(f'{h}={v}' for h, v in changes.items())}. Re-run with --yes to apply.",
            fg=typer.colors.YELLOW,
        )
        for r in matched:
            typer.echo(f"  {r['ID']}  [{r.get('状態', '')}]  {r.get('タイトル', '')}")
        return

    updates = [(r["ID"], changes) for r in matched]
    try:
        applied = ts.update_many(updates)
    except SheetError as e:
        _err(str(e))
    if json_out:
        typer.echo(json.dumps(applied, ensure_ascii=False))
    else:
        typer.secho(f"updated {len(applied)} tasks: {', '.join(applied)}", fg=typer.colors.GREEN)


@app.command(name="list")
def list_(
    status: Optional[Status] = typer.Option(None, "--status", help="Filter to a single 状態."),
    show_completed: bool = typer.Option(False, "--show-completed", help="Include 完了 tasks (hidden by default)."),
    tree: bool = typer.Option(False, "--tree", help="Show a WBS tree by 親ID (shows all; ignores --limit/--page)."),
    limit: int = typer.Option(50, "--limit", "-l", help="Max rows per page."),
    page: int = typer.Option(0, "--page", "-p", help="Page number, 0-indexed."),
    sheet: Optional[str] = SHEET_OPT,
    json_out: bool = typer.Option(False, "--json", help="Print rows as a JSON array."),
):
    """List tasks. By default 完了/キャンセル are hidden; --show-completed includes them.

    --tree renders a WBS hierarchy from 親ID with derived numbers (1.2.3).
    Ancestors of a matching task are always shown (dimmed) for context.
    """
    ts = _open(sheet)
    try:
        all_rows = ts.records()
    except SheetError as e:
        _err(str(e))

    def matches(r: dict) -> bool:
        if status is not None:
            return r.get("状態") == status.value
        if not show_completed:
            return r.get("状態") not in HIDDEN_BY_DEFAULT
        return True

    if tree:
        _render_tree(all_rows, matches, json_out)
        return

    rows = [r for r in all_rows if matches(r)]
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


def _render_tree(all_rows: list[dict], matches, json_out: bool) -> None:
    ordered, parent_map, flags, by_id = wbs_tree(all_rows)
    matched = {rid for rid, r in by_id.items() if matches(r)}
    # include ancestors of every match as context
    visible = set(matched)
    for rid in matched:
        cur = rid
        while cur in parent_map:
            cur = parent_map[cur]
            visible.add(cur)

    if json_out:
        out = []
        for rid, depth, wbs in ordered:
            if rid not in visible:
                continue
            rec = dict(by_id[rid])
            rec["wbs"] = wbs
            rec["depth"] = depth
            rec["context"] = rid not in matched
            rec["flags"] = sorted(flags.get(rid, ()))
            out.append(rec)
        typer.echo(json.dumps(out, ensure_ascii=False))
        return

    printed = False
    for rid, depth, wbs in ordered:
        if rid not in visible:
            continue
        printed = True
        r = by_id[rid]
        line = f"{wbs:<8}{'  ' * depth}{rid}  [{r.get('状態', '')}]  {r.get('タイトル', '')}"
        if r.get("作業者"):
            line += f"  @{r['作業者']}"
        if r.get("完了予定日"):
            line += f"  〆{r['完了予定日']}"
        fl = flags.get(rid, ())
        if "cycle" in fl:
            line += " (循環)"
        if "orphan" in fl:
            line += " (親?)"
        # ancestors shown only for context are dimmed
        typer.secho(line, fg=None if rid in matched else typer.colors.BRIGHT_BLACK)
    if not printed:
        typer.secho("(no tasks)", fg=typer.colors.BRIGHT_BLACK)


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


@sheet_app.command("repair")
def sheet_repair(
    sheet: Optional[str] = SHEET_OPT,
    yes: bool = typer.Option(False, "--yes", "-y", help="Apply the repair; without it, only a dry-run preview."),
    backup: bool = typer.Option(
        True, "--backup/--no-backup", help="Copy the sheet to a backup tab before applying (default: on)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Print the repair plan as JSON."),
):
    """Reconcile an existing sheet's columns to the current schema.

    Matches columns by header name, reorders them to the schema order, adds any
    missing columns (empty), and preserves unknown columns on the right. Useful
    after the column set changes. Dry-run by default; pass --yes to apply (a
    backup tab is created unless --no-backup).
    """
    ts = _open(sheet, ensure_header=False)
    try:
        plan = ts.repair_header(apply=yes, backup=backup)
    except SheetError as e:
        _err(str(e))

    if json_out:
        typer.echo(json.dumps(plan, ensure_ascii=False))
        return
    if plan["already_ok"]:
        typer.secho("header already matches the schema; nothing to do.", fg=typer.colors.GREEN)
        return

    typer.echo(f"current: {plan['current']}")
    typer.echo(f"target:  {plan['target']}")
    if plan["missing"]:
        typer.secho(f"  + add missing columns: {plan['missing']}", fg=typer.colors.YELLOW)
    if plan["extras"]:
        typer.secho(f"  ~ keep unknown columns on the right: {plan['extras']}", fg=typer.colors.YELLOW)
    typer.echo(f"  data rows: {plan['data_rows']}")

    if not plan["applied"]:
        typer.secho("dry-run: re-run with --yes to apply.", fg=typer.colors.YELLOW)
        return
    if plan.get("backup"):
        typer.secho(f"backup created: tab '{plan['backup']}'", fg=typer.colors.BRIGHT_BLACK)
    typer.secho("header repaired.", fg=typer.colors.GREEN)


@sheet_app.command("view")
def sheet_view(
    sheet: Optional[str] = SHEET_OPT,
    name: str = typer.Option("WBS", "--name", help="Name of the view tab to (re)generate."),
    json_out: bool = typer.Option(False, "--json", help="Print a JSON summary."),
):
    """Generate a human-friendly, collapsible WBS view in a separate tab.

    Reads the data sheet and writes a formatted 親ID tree to the view tab:
    WBS numbers + indentation, color by 状態, bold parents, frozen header, and
    native collapsible row groups. The tab is rebuilt from scratch each run and
    protected (warning-only). The data sheet is never modified.
    """
    ts = _open(sheet)
    try:
        info = ts.build_view(view_title=name)
    except SheetError as e:
        _err(str(e))
    if json_out:
        typer.echo(json.dumps(info, ensure_ascii=False))
    else:
        typer.secho(f"view '{info['view']}' updated ({info['rows']} tasks).", fg=typer.colors.GREEN)


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
