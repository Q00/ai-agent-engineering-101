# Adaptive pipeline implementation status

This file tracks implementation against `adaptive_pipeline.seed.yaml`.

- [x] Shared append-only control database, deterministic assignment and mutation lifecycle
- [ ] Buyer structured decisions, idempotent offers and recovery checkpoints
- [ ] Seller structured quotes, safety snapshots and recovery checkpoints
- [ ] Official-input freshness and rule invalidation controller
- [ ] Experiment statistics and promotion lifecycle
- [ ] Forum trust/publication controls
- [ ] Background supervisor start/stop/status/watch
- [ ] Role drain, checkpoint, canary and rollback
- [ ] Windows start/recovery scripts
- [ ] Rank milestones, velocity and leader-defense controller
- [ ] Full Seed acceptance test suite
- [ ] Foreground-to-supervisor operational handoff
