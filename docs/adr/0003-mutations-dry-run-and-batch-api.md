# 0003. Bulk/destructive ops: dry-run by default + batched API

- Status: Accepted
- Date: 2026-06-21

## Context

Operations that touch many rows at once — filter update (`--where/--set`), bulk
add/update (`--from`), and `sheet repair` — are both **dangerous** (one command
can change or overwrite a lot) and **API-heavy** (per-row calls hit Google's rate
limits fast). The tool is also driven by LLMs, so a blocking interactive prompt
would break automation.

## Decision

- **Dry-run by default for destructive/broad ops.** Filter update and
  `sheet repair` preview what they would do and apply nothing until an explicit
  `--yes`. Where an op rewrites data (`repair`), it also takes a backup first
  (unless `--no-backup`).
- **Batch the Sheets API.** Bulk writes go through one request:
  `append_rows` (add), `update_cells` (update), `batch_update` (repair/view).

## Consequences

- Safe by default, yet automation-friendly: an agent passes `--yes` deliberately
  rather than being blocked by a prompt.
- Far fewer API calls; resilient to rate limits; closer to atomic.
- Validation runs **before** any network/auth call so input errors surface as
  input errors, not auth errors.

## Alternatives considered

- **Apply immediately with an interactive confirm prompt** — rejected: blocks
  non-interactive/LLM use; `--yes` + dry-run gives the same safety.
- **Per-row writes in a loop** — rejected: rate-limit failures, partial writes,
  slow.
