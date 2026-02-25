# Proposal: Inline dashboard action failures (non-blocking bulk Resume/Pause/Delete)

The dashboard currently uses a blocking modal dialog (`messageBox`) to report partial failures when applying bulk
actions to multiple accounts (e.g. **Resume**). This interrupts the operator workflow and requires an explicit
dismissal before the accounts table can be inspected.

This change makes partial failures non-blocking by default:

- Show a toast summary for partial failures (with an optional "View details" action).
- Persist the per-account failure message client-side and surface it inline in the Accounts table and focused
  account panel.

Non-goals:

- No backend API/contract changes.
- No persistence of UI-only failure state across page reloads.

