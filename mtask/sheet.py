"""Google Sheets backend (gspread)."""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

import gspread
from gspread.auth import local_server_flow

from . import config

HEADERS = ["ID", "起票日", "更新日", "状態", "完了予定日", "起票者", "作業者", "タイトル", "状況"]

# Field key -> header label, used by the CLI to map flags to columns.
FIELDS = {
    "id": "ID",
    "created": "起票日",
    "updated": "更新日",
    "status": "状態",
    "due": "完了予定日",
    "reporter": "起票者",
    "assignee": "作業者",
    "title": "タイトル",
    "note": "状況",
}

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


def build_client(oauth_port: int | None = None) -> gspread.Client:
    """Build a gspread client.

    Default: OAuth user authentication via a local browser flow (a temporary
    HTTP server catches the redirect at http://localhost:<port>/). The first
    call opens a browser; the token is then cached at
    ~/.config/mtask/authorized_user.json.

    The local server listens on `oauth_port` if given, else the configured
    `[auth] port` (default 0 = an OS-chosen ephemeral port).

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

    def flow(client_config, scopes, port=port):
        # gspread calls flow(client_config, scopes) without a port, so the
        # chosen port is captured here as the default argument.
        return local_server_flow(client_config, scopes, port=port)

    return gspread.oauth(
        flow=flow,
        credentials_filename=str(client_secret),
        authorized_user_filename=str(config.authorized_user_path()),
    )


# Backwards-compatible alias.
_client = build_client


class TaskSheet:
    def __init__(self, spreadsheet_id: str):
        self._ws = build_client().open_by_key(spreadsheet_id).sheet1
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

    def _next_id(self, records: list[dict[str, Any]]) -> str:
        mx = 0
        for r in records:
            v = str(r.get("ID", ""))
            if v.startswith("T-"):
                try:
                    mx = max(mx, int(v[2:]))
                except ValueError:
                    pass
        return f"T-{mx + 1:04d}"

    def add(
        self,
        *,
        title: str,
        status: str,
        reporter: str,
        assignee: str,
        note: str,
        due: str = "",
    ) -> dict[str, Any]:
        records = self.records()
        now = _now()
        task = {
            "ID": self._next_id(records),
            "起票日": now,
            "更新日": now,
            "状態": status,
            "完了予定日": due,
            "起票者": reporter,
            "作業者": assignee,
            "タイトル": title,
            "状況": note,
        }
        self._ws.append_row([task[h] for h in HEADERS], value_input_option="USER_ENTERED")
        return task

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
