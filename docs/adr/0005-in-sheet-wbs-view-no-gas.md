# 0005. In-sheet WBS view via the CLI, not GAS

- Status: Accepted
- Date: 2026-06-21

## Context

People browsing the spreadsheet itself want a readable, collapsible WBS — not
just the CLI's `list --tree`. The obvious idea is to embed Google Apps Script
(GAS) to render and live-refresh a view. The feature that makes GAS attractive is
the **native collapsible outline** (the +/- row grouping).

Key realization: that outline is reachable from the **Sheets API directly**
(`addDimensionGroup`), so GAS isn't required to get the nice UX.

## Decision

Generate a formatted **view tab** from the CLI via the Sheets API
(`TaskSheet.build_view`): WBS numbers + indentation, native collapsible row
groups, color-by-状態 (conditional formatting), bold parents, frozen header, and a
warning-only protected range. The tab is deleted and recreated on each run so
stale formatting/groups never accumulate; the data sheet is never modified.
Refresh is **on-demand** (`mtask sheet view`).

## Consequences

- Uses the existing Sheets login — no extra OAuth scopes, no deployment.
- Single codebase (Python); tree logic shared with `list --tree` via `wbs_tree`.
- The view is a **snapshot**: re-run to refresh; it does not update live when
  someone edits the sheet by hand.

## Alternatives considered

- **GAS bound script** (onEdit/onOpen live refresh) — rejected for now: needs the
  Apps Script API + `script.projects` scope, per-sheet deployment (clasp/API), a
  second codebase to keep in sync, and has trigger limits. Revisit only if
  live auto-refresh on manual edits becomes a primary workflow.
- **Native features by hand only** (manual grouping/format) — rejected: not
  reproducible or scriptable.
