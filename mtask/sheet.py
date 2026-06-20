"""Google Sheets backend (gspread)."""

from __future__ import annotations

import datetime as dt
import os
import webbrowser
import wsgiref.simple_server
import wsgiref.util
from typing import Any

import gspread
from google_auth_oauthlib.flow import InstalledAppFlow

from . import config

# Field key -> header label. Order here defines the column order via HEADERS.
# Planned dates: 開始予定日 (plan_start) / 完了予定日 (due).
# Actual dates:  開始日 (start) / 完了日 (finish).
# Relationships: 親ID (parent, for WBS hierarchy) / 先行タスク (deps, predecessors).
FIELDS = {
    "id": "ID",
    "parent": "親ID",
    "created": "起票日",
    "status": "状態",
    "title": "タイトル",
    "summary": "概要",
    "reporter": "起票者",
    "assignee": "作業者",
    "deps": "先行タスク",
    "note": "状況",
    "plan_start": "開始予定日",
    "due": "完了予定日",
    "start": "開始日",
    "finish": "完了日",
    "updated": "更新日",
}

HEADERS = list(FIELDS.values())

# Input keys accepted by bulk/filter operations: either the English field key
# (matching the CLI flags) or the Japanese header. Maps to the English key.
INPUT_ALIASES = {**{k: k for k in FIELDS}, **{v: k for k, v in FIELDS.items()}}

STATUSES = ["未着手", "着手中", "完了", "保留", "キャンセル"]
DONE = "完了"
# Terminal states hidden from `list` unless --show-completed is passed.
HIDDEN_BY_DEFAULT = {"完了", "キャンセル"}
# Google Sheets allows up to 50,000 characters per cell.
NOTE_MAX_DEFAULT = 50000


class SheetError(RuntimeError):
    pass


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class _CaptureApp:
    """Minimal WSGI app: records the redirect request URI and shows a message.

    It accepts a request on ANY path, so the redirect endpoint path is whatever
    we advertise in redirect_uri.
    """

    def __init__(self, message: str):
        self.last_request_uri: str | None = None
        self._message = message

    def __call__(self, environ, start_response):
        start_response("200 OK", [("Content-type", "text/plain; charset=utf-8")])
        self.last_request_uri = wsgiref.util.request_uri(environ)
        return [self._message.encode("utf-8")]


class _QuietHandler(wsgiref.simple_server.WSGIRequestHandler):
    def log_message(self, *args):  # silence default stderr logging
        pass


def _local_server_flow(client_config, scopes, *, host="localhost", port=0, path="/"):
    """OAuth local-server flow with a configurable redirect endpoint path.

    Equivalent to InstalledAppFlow.run_local_server, but advertises a
    redirect_uri of http://<host>:<port><path> instead of always '/'.
    """
    flow = InstalledAppFlow.from_client_config(client_config, scopes)
    app = _CaptureApp("mtask: authentication complete — you can close this tab.")
    # Fail fast if the port is already in use.
    wsgiref.simple_server.WSGIServer.allow_reuse_address = False
    server = wsgiref.simple_server.make_server(host, port, app, handler_class=_QuietHandler)
    try:
        flow.redirect_uri = f"http://{host}:{server.server_port}{path}"
        # access_type=offline + prompt=consent ensures Google returns a
        # refresh_token; without it the cached authorized_user.json lacks one
        # and fails to load next time ("missing fields refresh_token").
        auth_url, _ = flow.authorization_url(
            access_type="offline", prompt="consent"
        )
        webbrowser.open(auth_url, new=1, autoraise=True)
        print(f"mtask: opening browser to authorize. If it doesn't open, visit:\n{auth_url}")
        server.handle_request()  # serve exactly one request (the redirect)
        # oauthlib requires https in the authorization response.
        authorization_response = app.last_request_uri.replace("http", "https")
        flow.fetch_token(authorization_response=authorization_response)
    finally:
        server.server_close()
    return flow.credentials


def build_client(
    oauth_port: int | None = None, oauth_path: str | None = None
) -> gspread.Client:
    """Build a gspread client.

    Default: OAuth user authentication via a local browser flow (a temporary
    HTTP server catches the redirect at http://localhost:<port><path>). The
    first call opens a browser; the token is then cached at
    ~/.config/mtask/authorized_user.json.

    The local server listens on `oauth_port` if given, else the configured
    `[auth] port` (default 0 = an OS-chosen ephemeral port). The redirect
    endpoint path is `oauth_path` if given, else `[auth] path` (default '/').

    Set auth method to 'service_account' (config or GOOGLE_APPLICATION_CREDENTIALS)
    for headless/server use.
    """
    # An explicit service-account key always wins (handy for CI / servers).
    sa_env = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if sa_env:
        return gspread.service_account(filename=sa_env)

    method = config.get_auth_method()  # default: "oauth"
    if method == "service_account":
        sa = config.service_account_path()
        if not sa.exists():
            raise SheetError(
                "auth method is 'service_account' but no key found. "
                f"Set GOOGLE_APPLICATION_CREDENTIALS or place a key at {sa}"
            )
        return gspread.service_account(filename=str(sa))

    # OAuth user flow (default).
    client_secret = config.oauth_client_path()
    if not client_secret.exists():
        raise SheetError(
            "OAuth client secret not found at "
            f"{client_secret}.\n"
            "Create an OAuth client ID of type 'Desktop app' in Google Cloud, "
            "download the JSON, and save it there. Then run `mtask auth login`."
        )

    port = config.get_auth_port() if oauth_port is None else oauth_port
    path = config.get_auth_path() if oauth_path is None else config.normalize_auth_path(oauth_path)

    # Self-heal a malformed cached token (e.g. missing refresh_token, written
    # by an older build): drop it so the browser flow re-runs instead of
    # raising "Authorized user info was not in the expected format".
    _drop_invalid_authorized_user()

    def flow(client_config, scopes):
        return _local_server_flow(client_config, scopes, port=port, path=path)

    return gspread.oauth(
        flow=flow,
        credentials_filename=str(client_secret),
        authorized_user_filename=str(config.authorized_user_path()),
    )


def _drop_invalid_authorized_user() -> None:
    """Remove the cached OAuth token if it can't be loaded as valid creds.

    gspread.oauth loads the token eagerly; a token without a refresh_token (or
    otherwise malformed) makes it raise before the flow can re-run. Deleting it
    lets the next call fall through to a fresh browser login.
    """
    token_path = config.authorized_user_path()
    if not token_path.exists():
        return
    try:
        import json

        from google.oauth2.credentials import Credentials

        with open(token_path, encoding="utf-8") as f:
            info = json.load(f)
        Credentials.from_authorized_user_info(info)
    except Exception:
        try:
            token_path.unlink()
        except OSError:
            pass


# Backwards-compatible alias.
_client = build_client


class TaskSheet:
    def __init__(self, spreadsheet_id: str, *, ensure_header: bool = True):
        self._ws = build_client().open_by_key(spreadsheet_id).sheet1
        # `repair` opens with ensure_header=False so it can fix a mismatched
        # header instead of erroring out on it.
        if ensure_header:
            self._ensure_header()

    def _ensure_header(self) -> None:
        first = self._ws.row_values(1)
        if first != HEADERS:
            if not first:
                self._ws.update([HEADERS], "A1")
            elif first[: len(HEADERS)] != HEADERS:
                raise SheetError(
                    f"sheet header mismatch.\n  expected: {HEADERS}\n  found:    {first}"
                )

    def records(self) -> list[dict[str, Any]]:
        return self._ws.get_all_records(expected_headers=HEADERS)

    def _max_num(self, records: list[dict[str, Any]]) -> int:
        mx = 0
        for r in records:
            v = str(r.get("ID", ""))
            if v.startswith("T-"):
                try:
                    mx = max(mx, int(v[2:]))
                except ValueError:
                    pass
        return mx

    def _next_id(self, records: list[dict[str, Any]]) -> str:
        return f"T-{self._max_num(records) + 1:04d}"

    def _build_task(self, fields: dict[str, str], task_id: str, now: str) -> dict[str, Any]:
        task = {h: "" for h in HEADERS}
        task.update(
            {
                "ID": task_id,
                "起票日": now,
                "状態": fields.get("status", STATUSES[0]),
                "更新日": now,
            }
        )
        # Map any provided English field keys (except auto-managed ones) onto
        # their columns.
        for key, header in FIELDS.items():
            if key in ("id", "created", "status", "updated"):
                continue
            if key in fields:
                task[header] = fields[key]
        return task

    def add(self, fields: dict[str, str]) -> dict[str, Any]:
        """Append a single task from an English-key field dict (see add_many)."""
        return self.add_many([fields])[0]

    def add_many(self, items: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Append several tasks in one request. IDs are auto-assigned in order.

        Each item is a dict of English field keys (title required; the rest —
        status, reporter, assignee, note, summary, parent, deps, and the date
        fields — optional). Values are assumed already validated.
        """
        if not items:
            return []
        records = self.records()
        now = _now()
        base = self._max_num(records)
        tasks = [self._build_task(it, f"T-{base + i:04d}", now) for i, it in enumerate(items, start=1)]
        self._ws.append_rows(
            [[t[h] for h in HEADERS] for t in tasks],
            value_input_option="USER_ENTERED",
        )
        return tasks

    def update_many(self, updates: list[tuple[str, dict[str, str]]]) -> list[str]:
        """Apply per-ID changes in one batch request.

        `updates` is a list of (task_id, changes) where changes maps Japanese
        headers to values. 更新日 is set automatically for every touched row.
        Raises SheetError if any ID is missing (nothing is written).
        """
        if not updates:
            return []
        ids = self._ws.col_values(1)  # column A, including the header row
        id_to_row = {v: i + 1 for i, v in enumerate(ids)}
        now = _now()
        cells: list[gspread.Cell] = []
        applied: list[str] = []
        for task_id, changes in updates:
            row = id_to_row.get(task_id)
            if row is None:
                raise SheetError(f"task '{task_id}' not found")
            for header, value in {**changes, "更新日": now}.items():
                cells.append(gspread.Cell(row, HEADERS.index(header) + 1, value))
            applied.append(task_id)
        self._ws.update_cells(cells, value_input_option="USER_ENTERED")
        return applied

    def _find_row(self, task_id: str) -> int:
        cell = self._ws.find(task_id, in_column=1)
        if cell is None:
            raise SheetError(f"task '{task_id}' not found")
        return cell.row

    def get(self, task_id: str) -> dict[str, Any]:
        for r in self.records():
            if str(r.get("ID")) == task_id:
                return r
        raise SheetError(f"task '{task_id}' not found")

    def update(self, task_id: str, changes: dict[str, str]) -> dict[str, Any]:
        row = self._find_row(task_id)
        changes = {**changes, "更新日": _now()}
        for header, value in changes.items():
            col = HEADERS.index(header) + 1
            self._ws.update_cell(row, col, value)
        return self.get(task_id)

    def _backup(self) -> str:
        """Duplicate the worksheet to a timestamped backup tab; return its name."""
        ss = self._ws.spreadsheet
        title = f"backup_{self._ws.title}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        ss.duplicate_sheet(self._ws.id, new_sheet_name=title)
        return title

    def repair_header(self, *, apply: bool = False, backup: bool = True) -> dict[str, Any]:
        """Reconcile the sheet's columns to HEADERS, preserving data.

        Strategy: read everything, rebuild the table in memory mapping each
        column by its header name (first occurrence), then rewrite once.
        - reorders columns to HEADERS order,
        - adds any missing columns (empty),
        - keeps unknown/duplicate columns on the right (no data dropped).

        With apply=False this only computes the plan (dry-run). With apply=True
        it rewrites the sheet (clearing cell formatting/formulas — values are
        kept) and, if backup=True, first copies the sheet to a backup tab.
        """
        values = self._ws.get_all_values()

        # Empty sheet (or a blank header row): just lay down the header.
        if not values or not any(values[0]):
            plan = {
                "current": values[0] if values else [],
                "target": HEADERS,
                "missing": HEADERS,
                "extras": [],
                "data_rows": max(0, len(values) - 1),
                "already_ok": False,
                "applied": False,
                "backup": None,
            }
            if apply:
                self._ws.update([HEADERS], "A1")
                plan["applied"] = True
            return plan

        current = [h.strip() for h in values[0]]
        # header name -> first source column index
        src: dict[str, int] = {}
        for i, name in enumerate(current):
            if name and name not in src:
                src[name] = i

        consumed = {src[h] for h in HEADERS if h in src}
        # Unknown / duplicate columns that still carry a header or data.
        leftover = [
            i
            for i, name in enumerate(current)
            if i not in consumed
            and (name or any(i < len(row) and row[i] != "" for row in values[1:]))
        ]
        extras = [current[i] if current[i] else f"列{i + 1}" for i in leftover]
        target = HEADERS + extras
        missing = [h for h in HEADERS if h not in src]

        plan: dict[str, Any] = {
            "current": current,
            "target": target,
            "missing": missing,
            "extras": extras,
            "data_rows": len(values) - 1,
            "already_ok": current == target,
            "applied": False,
            "backup": None,
        }
        if plan["already_ok"] or not apply:
            return plan

        src_cols = [src.get(h) for h in HEADERS] + leftover  # source index per target column
        new_rows = [target]
        for row in values[1:]:
            new_rows.append([row[j] if (j is not None and j < len(row)) else "" for j in src_cols])

        if backup:
            plan["backup"] = self._backup()
        self._ws.clear()
        self._ws.resize(rows=max(len(new_rows), 1), cols=len(target))
        self._ws.update(new_rows, "A1", value_input_option="RAW")
        plan["applied"] = True
        return plan
