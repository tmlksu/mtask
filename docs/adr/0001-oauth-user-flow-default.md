# 0001. OAuth user flow as the default auth

- Status: Accepted
- Date: 2026-06-21 (recorded; decided early in development)

## Context

mtask talks to Google Sheets via gspread, which supports two auth styles: a
**service account** (a robot identity with its own key) or an **OAuth user flow**
(you log in as yourself in a browser). The tool is a CLI for individuals (and
LLMs acting on their behalf), usually run locally against spreadsheets the person
already owns or can edit.

With a service account, every spreadsheet must first be **shared** with the
robot's email, and the user has to manage a JSON key file. That's friction for
the common "it's my own sheet" case.

## Decision

Default to the **OAuth user browser flow**: the first call opens a browser and a
temporary local HTTP server catches the redirect at
`http://localhost:<port><path>` (both configurable). The token is cached at
`~/.config/mtask/authorized_user.json`. A **service account** remains available
for headless/server use, and `GOOGLE_APPLICATION_CREDENTIALS` always wins.

## Consequences

- Any spreadsheet the user can already edit "just works" — no sharing step.
- First run requires an interactive browser; not suitable for cron/CI unless you
  switch to a service account.
- We must request offline access so the cached token carries a `refresh_token`
  (otherwise reloads fail) — implemented in the flow.
- The user supplies a Desktop-app OAuth client at `~/.config/mtask/oauth_client.json`.

## Alternatives considered

- **Service account as default** — rejected: forces sharing each sheet with the
  SA email and managing a key; worse for the primary single-user case. Kept as
  the headless alternative.
- **API key** — not viable for authorized read/write of a user's private sheets.
