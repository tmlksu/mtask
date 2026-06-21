# 0004. `sheet repair` via read → remap → rewrite

- Status: Accepted
- Date: 2026-06-21

## Context

The schema's column set evolves (e.g. adding WBS/scheduling fields). The header
is fixed and checked on every run, so an existing populated sheet with an older
or differently-ordered layout fails the check. We need a migration that
reconciles a sheet's columns to the current schema **without losing data**.

The intuitive algorithm — match columns by name, reorder, add missing — is right.
The question was *how to execute it*: move columns in place, or rebuild.

## Decision

Implement `sheet repair` as **read everything → rebuild the table in memory →
write once**: map each target column to its source by header name (first
occurrence), reorder to schema order, add missing columns (empty), and keep
unknown/duplicate columns on the right so nothing is dropped. Dry-run by default;
on `--yes` it copies the sheet to a `backup_<name>_<ts>` tab, then rewrites.

## Consequences

- Simple and robust: no column-index juggling, ~2–3 API calls, no partial-move
  failure states; unknown columns preserved.
- The rewrite keeps cell **values** but drops cell **formatting and formulas** —
  acceptable for a data sheet, and the backup tab is the safety net.
- `repair` must open the sheet with the header check disabled
  (`ensure_header=False`) since the whole point is a mismatched header.

## Alternatives considered

- **In-place column moves** (`insert`/`delete`/`moveDimension`) — rejected:
  shifting indices, many API calls, partial-failure risk, more code. It *would*
  preserve formatting/formulas; revisit only if that becomes a requirement.
