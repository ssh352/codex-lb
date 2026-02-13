## Dashboard Usage: Quota Reset & Pace (Context)

### What “Reset in 2d” means

The dashboard shows a “Quota pace (7D)” tile that includes a “Reset in …” label. This label is
based on the **secondary (7d) usage window** reset timestamp (`resetAt`).

Important nuance: the backend derives the secondary summary reset timestamp as the **earliest
reset time across all accounts** that have a known secondary reset. This makes the countdown and
pace target conservative.

So “Reset in 2d” means:

- At least one account’s 7‑day quota window will reset in ~2 days.
- Other accounts may reset later; check the per-account “Quota reset” values for account-specific
  reset times.

### Rounding behavior

The dashboard displays “in Xm / in Xh / in Xd” using a ceiling rounding strategy. For example,
“in 2d” can mean between a bit over 1 day and up to 2 days remaining, depending on the exact
timestamp.

### Example

If you have two accounts with secondary reset times:

- Account A resets in 2 days
- Account B resets in 5 days

Then the “Quota pace (7D)” tile will show “Reset in 2d”, while the account list/cards will show
each account’s own reset.

