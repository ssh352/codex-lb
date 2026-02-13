## Dashboard Waste Pacing (Secondary): Context

### What question this answers

“Zero waste” in this context means:

- Using secondary (7d) credits fast enough that **they do not expire unused** at the next secondary reset.

### How the signal is computed

We use a simple and explainable heuristic:

- Compute the **window-to-date average** burn rate in credits/hour.
- Assume it continues for the remaining time until reset.
- Project how many credits would remain unused at reset (“projected waste”).

This is intentionally conservative and operationally useful; it is not intended as a predictive model.

### Edge cases / unknowns

- Missing reset timestamps or missing/invalid window durations produce `null` rates and projections.
- When the window is very early (`elapsed_s == 0`), current rate is unknown and projections are `null`.

### UX notes

- Summary card should provide a single at-a-glance status (“On track” vs “Wasting ~X credits”).
- Per-account card should show the local projection and the required credits/hour to reach ~0 waste.

