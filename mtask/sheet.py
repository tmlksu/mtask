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

# Human-friendly WBS view tab (built by `sheet view`).
VIEW_TITLE_DEFAULT = "WBS"
VIEW_HEADERS = ["WBS", "ID", "状態", "タイトル", "概要", "作業者", "開始予定日", "完了予定日"]
# Background color per 状態 for the view (RGB 0..1).
STATUS_COLORS = {
    "未着手": (0.95, 0.95, 0.95),
    "着手中": (0.82, 0.89, 0.99),
    "完了": (0.85, 0.94, 0.83),
    "保留": (1.00, 0.95, 0.80),
    "キャンセル": (0.96, 0.80, 0.80),
}
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


def wbs_tree(
    records: list[dict[str, Any]],
) -> tuple[list[tuple[str, int, str]], dict[str, str], dict[str, set], dict[str, dict]]:
    """Build a WBS tree from 親ID links.

    Returns (ordered, parent_map, flags, by_id) where:
      - ordered: [(id, depth, wbs_number)] in display order (DFS, siblings by ID),
      - parent_map: child id -> parent id (only real tree edges),
      - flags: id -> set of {'orphan','cycle'},
      - by_id: id -> record.
    Orphans (親ID missing/self) and cycle members are treated as roots so the
    traversal always terminates.
    """
    by_id = {str(r.get("ID")): r for r in records if str(r.get("ID", ""))}

    def raw_parent(rid: str) -> str:
        return str(by_id[rid].get("親ID") or "").strip()

    def valid_parent(rid: str):
        p = raw_parent(rid)
        return p if (p and p in by_id and p != rid) else None

    def in_cycle(rid: str) -> bool:
        seen: set[str] = set()
        cur = rid
        while True:
            p = valid_parent(cur)
            if p is None:
                return False
            if p == rid or p in seen:
                return True
            seen.add(p)
            cur = p

    children: dict[str, list[str]] = {}
    roots: list[str] = []
    flags: dict[str, set] = {}
    for rid in by_id:
        rp = raw_parent(rid)
        p = valid_parent(rid)
        f: set[str] = set()
        if rp and (rp not in by_id or rp == rid):
            f.add("orphan")
        if p is not None and in_cycle(rid):
            f.add("cycle")
            p = None
        flags[rid] = f
        (roots if p is None else children.setdefault(p, [])).append(rid)

    roots.sort()
    for cs in children.values():
        cs.sort()
    parent_map = {c: p for p, cs in children.items() for c in cs}

    ordered: list[tuple[str, int, str]] = []

    def dfs(rid: str, depth: int, wbs: str) -> None:
        ordered.append((rid, depth, wbs))
        for i, c in enumerate(children.get(rid, []), start=1):
            dfs(c, depth + 1, f"{wbs}.{i}")

    for i, rid in enumerate(roots, start=1):
        dfs(rid, 0, str(i))
    return ordered, parent_map, flags, by_id


def _nodes_in_cycles(adj: dict[str, list[str]]) -> set[str]:
    """Return the set of nodes that lie on a directed cycle in `adj`."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in adj}
    in_cycle: set[str] = set()
    stack: list[str] = []

    def visit(u: str) -> None:
        color[u] = GRAY
        stack.append(u)
        for v in adj.get(u, []):
            if color.get(v, BLACK) == GRAY and v in stack:
                in_cycle.update(stack[stack.index(v):])
            elif color.get(v, BLACK) == WHITE:
                visit(v)
        stack.pop()
        color[u] = BLACK

    for n in list(adj):
        if color[n] == WHITE:
            visit(n)
    return in_cycle


def schedule_findings(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Report (don't fix) scheduling/relationship problems.

    Each finding is {severity: 'error'|'warning', type, id, message}. 'error'
    = structural integrity (cycles, dangling refs, inverted dates); 'warning'
    = soft schedule issues (predecessor not done, planned overlap).
    """
    _ordered, _pmap, flags, by_id = wbs_tree(records)
    ids = set(by_id)
    findings: list[dict[str, Any]] = []

    def add(sev: str, typ: str, rid: str, msg: str) -> None:
        findings.append({"severity": sev, "type": typ, "id": rid, "message": msg})

    def preds(rid: str) -> list[str]:
        return [x.strip() for x in str(by_id[rid].get("先行タスク") or "").split(",") if x.strip()]

    # parent integrity (reuse wbs_tree flags)
    for rid in by_id:
        fl = flags.get(rid, set())
        if "orphan" in fl:
            p = str(by_id[rid].get("親ID") or "").strip()
            if p == rid:
                add("error", "self_parent", rid, "親IDに自分自身を指定しています")
            else:
                add("error", "dangling_parent", rid, f"親ID '{p}' が存在しません")
        if "cycle" in fl:
            add("error", "parent_cycle", rid, "親IDが循環しています")

    # predecessor integrity + dependency graph
    adj: dict[str, list[str]] = {}
    for rid in by_id:
        valid: list[str] = []
        for p in preds(rid):
            if p == rid:
                add("error", "self_dependency", rid, "自分自身を先行タスクに指定しています")
            elif p not in ids:
                add("error", "dangling_predecessor", rid, f"先行タスク '{p}' が存在しません")
            else:
                valid.append(p)
        adj[rid] = valid
    for rid in _nodes_in_cycles(adj):
        add("error", "dependency_cycle", rid, "先行タスクが循環しています")

    # inverted date ranges within a task
    for rid, r in by_id.items():
        ps, pe = str(r.get("開始予定日", "")), str(r.get("完了予定日", ""))
        if ps and pe and ps > pe:
            add("error", "plan_date_inverted", rid, f"開始予定日({ps})が完了予定日({pe})より後です")
        as_, ae = str(r.get("開始日", "")), str(r.get("完了日", ""))
        if as_ and ae and as_ > ae:
            add("error", "actual_date_inverted", rid, f"開始日({as_})が完了日({ae})より後です")

    # predecessor-relative warnings
    for rid in by_id:
        r = by_id[rid]
        st = str(r.get("状態", ""))
        for p in adj[rid]:
            pr = by_id[p]
            if st in ("着手中", DONE) and str(pr.get("状態", "")) != DONE:
                add("warning", "predecessor_not_done", rid, f"{st}ですが先行タスク {p} が未完了です")
            ts, pe = str(r.get("開始予定日", "")), str(pr.get("完了予定日", ""))
            if ts and pe and ts < pe:
                add("warning", "starts_before_predecessor_due", rid,
                    f"開始予定日({ts})が先行 {p} の完了予定日({pe})より前です")

    sev_rank = {"error": 0, "warning": 1}
    findings.sort(key=lambda f: (f["id"], sev_rank[f["severity"]], f["type"]))
    return findings


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

    def build_view(self, *, view_title: str = VIEW_TITLE_DEFAULT) -> dict[str, Any]:
        """(Re)build a human-friendly, collapsible WBS view in a separate tab.

        Renders the 親ID tree with WBS numbers + indentation, color-codes rows by
        状態, bolds parent rows, freezes the header, and creates native row groups
        (the +/- outline) so the hierarchy collapses. The tab is recreated from
        scratch each run (so stale formatting/groups never accumulate) and is
        protected (warning-only). The data sheet is never modified.
        """
        if view_title == self._ws.title:
            raise SheetError(
                f"view tab name '{view_title}' must differ from the data sheet; "
                "pass --name to choose another."
            )

        ordered, _parent_map, flags, by_id = wbs_tree(self.records())

        # value matrix --------------------------------------------------------
        rows: list[list[str]] = [VIEW_HEADERS]
        for rid, depth, wbs in ordered:
            r = by_id[rid]
            mark = ""
            fl = flags.get(rid, ())
            if "cycle" in fl:
                mark += " (循環)"
            if "orphan" in fl:
                mark += " (親?)"
            rows.append(
                [
                    wbs,
                    rid,
                    str(r.get("状態", "")),
                    "  " * depth + str(r.get("タイトル", "")) + mark,
                    str(r.get("概要", "")),
                    str(r.get("作業者", "")),
                    str(r.get("開始予定日", "")),
                    str(r.get("完了予定日", "")),
                ]
            )

        ss = self._ws.spreadsheet
        try:
            old = ss.worksheet(view_title)
            ss.del_worksheet(old)
        except gspread.WorksheetNotFound:
            pass
        view = ss.add_worksheet(title=view_title, rows=max(len(rows), 1), cols=len(VIEW_HEADERS))
        view.update(rows, "A1", value_input_option="USER_ENTERED")

        sid = view.id
        ncols = len(VIEW_HEADERS)
        n = len(ordered)
        # subtree end (exclusive ordered index) per node, from depths
        depths = [d for _, d, _ in ordered]

        requests: list[dict] = [
            # freeze header
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1}},
                    "fields": "gridProperties.frozenRowCount",
                }
            },
            # bold header
            {
                "repeatCell": {
                    "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": ncols},
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            },
        ]

        # color rows by 状態 (column C = 状態) via conditional formatting
        for status, (cr, cg, cb) in STATUS_COLORS.items():
            requests.append(
                {
                    "addConditionalFormatRule": {
                        "index": 0,
                        "rule": {
                            "ranges": [
                                {"sheetId": sid, "startRowIndex": 1, "endRowIndex": max(len(rows), 2), "startColumnIndex": 0, "endColumnIndex": ncols}
                            ],
                            "booleanRule": {
                                "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": f'=$C2="{status}"'}]},
                                "format": {"backgroundColor": {"red": cr, "green": cg, "blue": cb}},
                            },
                        },
                    }
                }
            )

        # bold parent rows + collapsible groups over each node's descendants
        for i in range(n):
            j = i + 1
            while j < n and depths[j] > depths[i]:
                j += 1
            if j == i + 1:
                continue  # no children
            parent_row = i + 1  # 0-based grid row (header at 0)
            requests.append(
                {
                    "repeatCell": {
                        "range": {"sheetId": sid, "startRowIndex": parent_row, "endRowIndex": parent_row + 1, "startColumnIndex": 0, "endColumnIndex": ncols},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                        "fields": "userEnteredFormat.textFormat.bold",
                    }
                }
            )
            requests.append(
                {
                    "addDimensionGroup": {
                        "range": {"sheetId": sid, "dimension": "ROWS", "startIndex": i + 2, "endIndex": j + 1}
                    }
                }
            )

        # protect the generated view (warning-only)
        requests.append(
            {
                "addProtectedRange": {
                    "protectedRange": {
                        "range": {"sheetId": sid},
                        "warningOnly": True,
                        "description": "mtask generated WBS view — regenerate with `mtask sheet view`",
                    }
                }
            }
        )

        ss.batch_update({"requests": requests})
        return {"view": view_title, "rows": n}
