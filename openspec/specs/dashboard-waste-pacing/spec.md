# Dashboard: Waste Pacing (Secondary)

## Requirements

- The dashboard overview API (`GET /api/dashboard/overview`) MUST expose a `waste_pacing` object (serialized as `wastePacing`) or `null`.
- `wastePacing` MUST target the **secondary (7d) usage window** only.
- For each account in `wastePacing.accounts`, the API MUST expose:
  - `currentRateCreditsPerHour` as a number or `null`.
  - `requiredRateCreditsPerHour` as a number or `null`.
  - `projectedWasteCredits` as a number or `null`.
  - `onTrack` as a boolean or `null`.
- The backend MUST compute waste pacing based on a window-to-date average burn rate:
  - When `elapsed_s = max(0, window_len_s - time_to_reset_s)` is `0`, `currentRateCreditsPerHour` MUST be `null`.
  - When `time_to_reset_s` is `0`, `requiredRateCreditsPerHour` MUST be `null`.
  - When `currentRateCreditsPerHour` is `null`, `projectedWasteCredits` and `onTrack` MUST be `null`.
- All timestamps in waste pacing payloads MUST be ISO 8601 strings (`datetime` in schemas) or `null`.

