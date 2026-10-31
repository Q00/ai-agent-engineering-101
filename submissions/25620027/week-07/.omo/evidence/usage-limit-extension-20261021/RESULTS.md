# AX7 cumulative limit extension — 2026-10-21 13:48 KST

(Codex) The user requested “ㅇㅇ 한도 늘려놔 5000” after a warning at4767/5000. The stated implementation was5000 additional calls,10000 cumulative. The21:00 KST deadline, immutable4354 baseline, persistent usage logs, signing identity, address and negotiation policy remain unchanged. No automatic buyer or rejoin was started.

## Observed result

- Before: seller1.3.1 running/healthy, stored4767/5000. [Before runtime](before-runtime.log).
- Built image1.3.1-limit10000, image ID sha256:a9700b2866680944e0f76e8b96d20a381910566d2c04b45839412b6f8bb0d782. [Build output](build.log), [archive hash](source-archive.sha256), [source allowlist](source-archive-files.txt). No credentials were copied.
- Recreated only the existing seller service at13:48:07. The immediate cutover file still shows the departing guard's5000/signal record; the subsequent read confirms the new guard. [Cutover](cutover.log).
- At13:48:28: seller running/healthy, limit10000, count4767, baseline4354, reasonrunning, deadline2026-10-21T21:00:00+09:00. Remaining observed capacity5233. Baseline SHA256 matches before/after; Hermes container ID/start/restart count are unchanged. [After runtime](after-runtime.log).
- Public Agent Card GET succeeded and reports1.3.1. [Card](agent-card.json). No extra virtual purchase or public review was required for this operating-limit change.
- Guard12/12 tests passed, including old5000 count resuming to5001 without a reset,10000 stop/restart behavior, absolute deadline, retained usage and baseline/log loss rejection. [Tests](guard-tests.log).
- Existing monitor ax7 remainsACTIVE and now stops at10000 or21:00 KST. Completed one-time evaluation ax7-1 remainsPAUSED. [Automation readback](automation-status.json). Prior95% warning is explicitly associated with its historical5000 limit.

MODEL_USAGE counts observed completed responses; in-flight responses can complete during shutdown. It is not a provider billing hard limit. This change does not establish a market improvement or rerun the earlier25/25 market checks.
