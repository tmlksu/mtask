# 0002. Sheet stores raw inputs; the CLI computes views

- Status: Accepted
- Date: 2026-06-21

## Context

As the tool grows toward WBS / scheduling, it's tempting to store *derived*
values in the spreadsheet: WBS numbers (1.2.3), progress rolled up from children,
computed schedules, critical path. But a spreadsheet has no triggers or
integrity guarantees — the moment any input changes, every stored derivative is
potentially stale and inconsistent, and nothing enforces it.

## Decision

The sheet stores **only raw inputs**: one row per task, with relationships kept
as **ID references** (`親ID`, `先行タスク`). Anything derived — WBS numbers, tree
order, dependency/cycle checks, the in-sheet view — is **computed at read time by
the CLI** and never persisted back to the data sheet.

## Consequences

- No class of "derived column went stale" bugs; the data sheet stays simple,
  portable, and hand-editable.
- Each read recomputes (cheap at this scale). Logic like cycle/orphan handling
  and WBS numbering lives in code (`wbs_tree`), where it's testable.
- Generated artifacts (e.g. the `WBS` view tab) are clearly separate and
  regenerable, not a source of truth.

## Alternatives considered

- **Store computed columns** (WBS no., rollups) — rejected: inconsistent on any
  edit, needs a reconciliation pass, fragile.
- **Move to an external database** — rejected: the spreadsheet *is* the point
  (shared, viewable, editable by humans).
