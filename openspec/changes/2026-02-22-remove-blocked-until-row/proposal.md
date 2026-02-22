# Proposal: Remove “Blocked until” row from Selected account

The dashboard currently renders a dedicated “Blocked until …” field when an account is blocked
(`rate_limited` / `quota_exceeded`) and `statusResetAt` is present.

This change removes the dedicated row and instead surfaces the same effective retry boundary inside the existing
reset rows (“Rate limit reset (5h)” / “Quota reset (7d)”) with a tooltip that clarifies the meaning.

Non-goals:

- No API/contract changes (`statusResetAt` remains exposed).
- No changes to backend routing behavior.
