# Design

## Scope

This change targets **secondary (7d) credits** only.

## Definitions

For each account `i`:

- `capacity_credits_i`: plan-dependent secondary capacity.
- `used_percent_i`: latest secondary used percent.
- `used_credits_i = capacity_credits_i * used_percent_i / 100`.
- `remaining_credits_i = max(0, capacity_credits_i - used_credits_i)`.
- `reset_at_epoch_i`: latest secondary reset time in epoch seconds.
- `window_minutes_i`: latest secondary window minutes; fallback to default secondary window minutes.
- `time_to_reset_s_i = max(0, reset_at_epoch_i - now_epoch)`.
- `window_len_s_i = window_minutes_i * 60`.
- `elapsed_s_i = max(0, window_len_s_i - time_to_reset_s_i)`.

### Current consumption rate (window-to-date average)

- If `elapsed_s_i > 0`: `current_rate_cph_i = (used_credits_i / elapsed_s_i) * 3600`, else `null`.

### Required rate to hit 0 waste (from now)

- If `time_to_reset_s_i > 0`: `required_rate_cph_i = (remaining_credits_i / time_to_reset_s_i) * 3600`, else `null`.

### Projected waste at reset

- If `current_rate_cph_i != null`:
  `projected_waste_i = max(0, remaining_credits_i - (current_rate_cph_i / 3600) * time_to_reset_s_i)`,
  else `null`.

### On-track

- `on_track_i = projected_waste_i <= 0.5` credits.

## API

Extend `GET /api/dashboard/overview` with `wastePacing`:

- Summary: totals + counts for quick “are we ok?” UX.
- Accounts: per-account pacing fields for the existing account cards.

All timestamps remain ISO 8601 strings via dashboard schemas.

## UI

- Add a dashboard stat card: “Zero-waste pacing (secondary)”.
- Extend account cards with a pacing line:
  - On track: “On track to 0 waste”
  - At risk: “Projected waste: ~X credits (need Y/hr)”
  - Unknown: “Waste pacing: --”
