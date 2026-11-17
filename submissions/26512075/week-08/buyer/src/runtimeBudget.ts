/** End before the kit's 300s kill; reserve time for cancellation and outcome persistence. */
export class RuntimeBudget {
  private readonly deadline: number;
  constructor(startedAt: number, expires?: number, private readonly clock = Date.now) {
    this.deadline = Math.min(startedAt + 270_000, expires === undefined ? Infinity : expires * 1000 - 10_000);
  }
  remaining(): number { return Math.max(0, this.deadline - this.clock()); }
  timeout(cap: number): number {
    const ms = Math.min(cap, this.remaining() - 5_000);
    if (ms <= 0) throw new Error("Buyer deadline reached");
    return Math.max(1, Math.floor(ms));
  }
  canRetry(): boolean { return this.remaining() >= 20_000; }
  cleanupTimeout(): number { return Math.max(1, Math.min(5_000, Math.floor(this.remaining()))); }
}
