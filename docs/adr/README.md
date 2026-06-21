# Architecture Decision Records

Short, append-only records of **why** non-trivial decisions were made — the
context, the choice, its consequences, and the alternatives we rejected.

- One file per decision: `NNNN-kebab-title.md` (zero-padded, sequential).
- Status: `Proposed` → `Accepted` → (later) `Superseded by NNNN` / `Deprecated`.
  Don't rewrite an accepted ADR to change its decision — add a new one that
  supersedes it, and update the old one's status line.
- Keep them short (MADR-lite). Write one only when there's a real choice with
  trade-offs; routine changes belong in commits / the [CHANGELOG](../../CHANGELOG.md).

See [`template.md`](template.md). Doc roles: README = how to use, HANDOFF = how
to work on it now, ADR = why it's built this way, CHANGELOG = what changed when.

## Index

- [0001](0001-oauth-user-flow-default.md) — OAuth user flow as the default auth
- [0002](0002-sheet-stores-raw-cli-computes.md) — Sheet stores raw inputs; the CLI computes views
- [0003](0003-mutations-dry-run-and-batch-api.md) — Bulk/destructive ops: dry-run by default + batched API
- [0004](0004-sheet-repair-rewrite-strategy.md) — `sheet repair` via read→remap→rewrite
- [0005](0005-in-sheet-wbs-view-no-gas.md) — In-sheet WBS view via the CLI, not GAS
- [0006](0006-schedule-check-report-only.md) — Schedule diagnostics: report-only, severity + CI exit code
