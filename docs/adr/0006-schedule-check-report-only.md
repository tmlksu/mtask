# 0006. Schedule diagnostics are report-only, with severity + CI exit code

- Status: Accepted
- Date: 2026-06-21

## Context

With WBS/scheduling fields in place (`親ID`, `先行タスク`, planned/actual dates),
data can become inconsistent: dependency or parent cycles, references to tasks
that don't exist, inverted date ranges, or schedules that violate dependencies.
We want to surface these. The open question is whether the tool should *fix* them
(e.g. auto-reschedule) or only *report* them.

## Decision

`schedule check` is **report-only** — it reads and reports, never writes.
Findings carry a **severity**: `error` for structural integrity problems
(cycles, dangling/self references, inverted date ranges) and `warning` for soft
schedule issues (a started/finished task whose predecessor isn't done; a planned
start before a predecessor's planned finish). Human and `--json` output are both
supported. The command **exits non-zero (1) when any errors exist** (warnings
alone exit 0), so it can gate CI.

## Consequences

- Safe and predictable; consistent with "store raw, don't mutate on read"
  (ADR-0002) and "report rather than auto-reschedule".
- Usable as a lint in automation via the exit code; severity lets users triage.
- Pure logic (`schedule_findings`) is unit-tested offline; date comparisons rely
  on ISO `YYYY-MM-DD` sorting lexically (validated on input).

## Alternatives considered

- **Auto-fix / auto-reschedule** — rejected: fragile on a spreadsheet, surprising,
  and easy to make things worse; a report lets the human decide.
- **Single severity / always exit non-zero on any finding** — rejected: normal
  in-progress projects routinely have warnings; that would make the check
  useless in CI. Errors-only gating is the useful default.
