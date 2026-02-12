# Tasks

- [x] Add configurable proxy snapshot TTL.
- [x] Add sticky sessions backend (`db` vs `memory`) and implement memory stickiness.
- [x] Set default sticky backend to `memory` (single-process default).
- [x] Add request log buffering + background flusher (enabled by default).
- [x] Make usage refresh fetch concurrent with configurable concurrency.
- [x] Clarify snapshot TTL “freshness” tradeoff and recommended values.
- [ ] Add perf regression benchmarks for proxy compact + streaming with sticky enabled.
