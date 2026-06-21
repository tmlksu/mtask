# 0007. Simple gantt rendered in the view tab (not the terminal)

- Status: Accepted
- Date: 2026-06-21

## Context

After the WBS view (ADR-0005), the next step was a simple gantt. It could render
in the terminal (ASCII bars in `list`) or extend the in-sheet view tab with a
date grid. The in-sheet WBS view is the surface meant for *people browsing the
spreadsheet*, and the gantt was originally framed as an in-sheet timeline.

## Decision

Extend `sheet view` with a gantt: append a **date grid** to the right of the WBS
columns and **background-color the cells** to form a bar across each task's span
(planned `開始予定日`/`完了予定日`, falling back to actual `開始日`/`完了日`), with a
今日 marker on the current column. Bucket granularity adapts to the range: daily
(≤31 days), weekly (≤~30 weeks), else monthly. Bars use stronger status colors;
the row-tint conditional formatting is confined to the left columns so it never
paints over the bars. Built in the same `build_view` pass.

## Consequences

- The human-facing WBS and its timeline live together in one tab, no extra
  command or surface.
- Reuses the existing view machinery (recreate-each-run, protected, data sheet
  untouched) and the shared `wbs_tree`.
- Bucket/bar math is pure (`_task_span`, `_gantt_buckets`) and unit-tested
  offline; only the Sheets `batch_update` formatting is untested without a live
  sheet.
- It's a snapshot (re-run to refresh), consistent with ADR-0005.

## Alternatives considered

- **Terminal ASCII gantt** (`list --gantt`) — lower risk and fully offline-
  testable, but the chosen audience is people viewing the spreadsheet; deferred,
  could still be added later as a CLI convenience.
- **Both at once** — deferred to keep the change focused.
