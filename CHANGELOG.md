# Changelog

All notable changes to mtask are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project aims to
follow [Semantic Versioning](https://semver.org/). The "why" behind larger
decisions lives in [docs/adr](docs/adr/).

## [Unreleased]

Nothing released yet — `0.1.0` is in development. Everything below is the work to
date, grouped for the first release.

### Added
- Spreadsheet-backed task CLI built on gspread: `add`, `update`, `list`, `get`,
  with typer, LLM-friendly help, and `--json` on every command.
- Per-project sheets: `sheet add` / `sheet list` / `sheet use` (slug → spreadsheet ID).
- Default reporter (起票者): `user` / `user set`.
- Auth (ADR-0001): OAuth user browser flow (default) and service account;
  `auth login/logout/method`; configurable local-server `auth port` and
  redirect `auth path`.
- Bulk add/update via `--from` (JSON array, `-` = stdin) and filter update via
  `--where`/`--set`, dry-run unless `--yes`; batched Sheets API (ADR-0003).
- WBS / scheduling fields: `親ID`, `概要`, `先行タスク`, `開始予定日`,
  `開始日`, `完了日` (planned/actual dates); inputs stored raw (ADR-0002).
- `list --tree` — WBS hierarchy from `親ID` with derived numbers (1.2.3),
  context ancestors, orphan/cycle flags.
- `sheet repair` — reconcile an existing sheet's columns to the schema via
  read→remap→rewrite; dry-run with backup tab (ADR-0004).
- `sheet view` — human-friendly, collapsible in-sheet WBS view tab via the
  Sheets API, no Apps Script (ADR-0005); includes a built-in **gantt** date grid
  with colored bars and a 今日 marker (ADR-0007).
- `schedule check` — report-only diagnostics for dependency/parent cycles,
  dangling/self references, inverted date ranges (errors) and soft schedule
  issues (warnings); exits non-zero on errors (ADR-0006).
- Docs: README, HANDOFF, ADRs, this changelog.

### Changed
- Sheet column order reordered for readability.

### Fixed
- OAuth flow requests offline access so the cached token includes a
  `refresh_token` (reloads no longer fail); malformed cached tokens self-heal.

[Unreleased]: https://github.com/tmlksu/mtask/commits/main
