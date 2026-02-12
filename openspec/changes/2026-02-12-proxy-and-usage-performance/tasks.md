# Tasks

- [x] Add configurable proxy snapshot TTL.
- [x] Add sticky sessions backend (`db` vs `memory`) and implement memory stickiness.
- [x] Set default sticky backend to `memory` (single-process default).
- [x] Add request log buffering + background flusher (enabled by default).
- [x] Make usage refresh fetch concurrent with configurable concurrency.
- [ ] Add perf regression benchmarks for proxy compact + streaming with sticky enabled.
