# Design

## UX goals

- Avoid interrupting operators after bulk actions (no forced modal dismissal).
- Make it obvious which accounts failed and why, without searching logs.
- Preserve access to copyable, high-detail failure output when needed.

## UI behavior

### Bulk actions (`_bulkCall`)

When a bulk action is applied to one or more accounts:

- If all requests succeed: show the existing success toast.
- If one or more requests fail:
  - Show a warning toast with a short summary and an optional **View details** action.
  - Store the failure message (and upstream details if available) keyed by `accountId` in client state.
  - Clear any previous stored failure for accounts that succeeded in this bulk action.

The **View details** action opens the existing `messageBox` with a copyable newline-delimited list of
`accountId: message`.

### Accounts table

For any row with a stored failure entry:

- Render a small inline indicator next to the Status pills.
- Hover title shows the failure message.
- Click opens a detail modal showing:
  - account label + id
  - action label
  - timestamp (`atIso`)
  - HTTP status (when available)
  - structured payload error details (when available)

### Focused account panel

When the focused account has a stored failure entry:

- Render a "Last action error" row with the failure message and a **Details** button that opens the same modal.

## Client state

Extend the accounts state with:

- `accounts.lastActionFailuresById: Record[str, FailureEntry]`

Where `FailureEntry` is a client-only object:

- `action: str`
- `atIso: str` (ISO 8601)
- `message: str`
- `status?: int`
- `payload?: any` (best-effort printable; may include `error.code`, `error.message`, `error.details`)

The map is pruned on refresh to remove entries for accounts that no longer exist in `accounts.rows`.

