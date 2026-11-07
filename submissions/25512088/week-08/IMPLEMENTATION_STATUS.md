# Adaptive pipeline implementation status

This file tracks implementation against `adaptive_pipeline.seed.yaml`.

- [x] Shared append-only control database, deterministic assignment and mutation lifecycle
- [x] Buyer structured decisions, idempotent offers and recovery checkpoints
- [x] Seller structured quotes, safety snapshots and recovery checkpoints
- [x] Official-input freshness and rule invalidation controller
- [x] Experiment statistics and promotion lifecycle
- [x] Forum trust/publication controls
- [x] Background supervisor start/stop/status/watch
- [x] Role drain, checkpoint, canary and rollback
- [ ] Windows start/recovery scripts
- [ ] Rank milestones, velocity and leader-defense controller
- [ ] Full Seed acceptance test suite
- [ ] Foreground-to-supervisor operational handoff
