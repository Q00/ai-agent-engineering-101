# Week07 Mac to Hermes VPS migration — 2026-10-21

## Scope and result

The user explicitly requested migration to the existing Hermes VPS72.62.196.24. Source package2acfe4b runs the unchanged1.2.0 seller, same gpt-4.1-nano model, receipt key, name25620027 and public URLhttps://ax-week07.treasuresky.xyz. No market rejoin or automatic buyer was started. Existing Hermes container ID6d0343b4ddfc965b534d1c0ea4f5e4bdebbd923d46ea221965e058050e1b1998/start2026-10-18T23:26:50Z and restart count0 remained unchanged; the existing Week06 container remained running/healthy. These health observations are not new Week06 transaction tests.

## Reproduction and preparation

- Dedicated root `/docker/ax-week07-25620027`; container `ax-week07-25620027-seller-1`; single guard supervises seller+cloudflared. UID10001,1CPU,768MiB,128PIDs,no published host ports,no-new-privileges,all capabilities dropped,on-failure3. No container or service was added inside Hermes.
- Frozen harness lock exported and installed with hashes. Base images:Python3.12.11-slim,uv0.9.5,cloudflared2026.10.1. Source upload was allowlisted; no private data entered image context.
- Existing OpenAI environment, seller Ed25519 private key and assignment-only tunnel credential were transferred only over SSH to the dedicated private directory,0700/0600. They are not in Git/logs. The signing key is copied from read-only private mount into writable runtime tmpfs so ReceiptSigner chmod succeeds. The same registered public key was verified on the actual VPS before cutover.
- Staging used separate persistent directories with external tunnel and comparison disabled. Staging expected no model requests. `staging-runtime-attempt-2.log`:version1.2.0,public HTTPS interface,registered key match and signature vector passed.
- Guard11 scenarios plus four ASGI public route checks passed. Independent review initially BLOCKed a counter rollback after log loss; durable checkpoint+log lower-bound validation fixed it and review APPROVEd. The5000 threshold is the existing observed MODEL_USAGE condition; in-flight requests may append a few shutdown records. It is not a strict upper bound on all API attempts.

## Preserved attempts

- Generic worker role was rejected by routing hook; a registered medium worker was used instead.
- The initial private-transfer helper lacked local PyYAML and exited before reading/transferring values; standard-library JSON (valid YAML) was used successfully.
- Docker Compose `run --no-build` is unsupported on this server; it was removed and the already built image was used.
- First staging failed because build root owned /tmp/uv-cache. Dockerfile now separates nonroot runtime cache at /home/app/.cache/uv. Rebuilt image and actual staging passed; raw staging log is preserved.
- Original Mac recovery watcher received SIGINT but its cleanup hit an OS PermissionError after stopping the tunnel. The exact old kit43272 and caffeinate35989 were then explicitly confirmed and stopped; no duplicate public tunnel was started while they were active.

## Cutover and independent public test

Mac final observed usage4354 was frozen after its seller stopped. Production uses immutable baseline4354 plus its durable seller.log and guard-state; changing or losing required state fails closed. Production started **08:47:36 KST**. The public URL was tested with the Mac seller and tunnel off: at **08:48:01**, lamp opening100, infeasible offer1 counter100, next offer100 accepted. Receipt price/item/task and Ed25519 signature matched the market-registered key. No market result or public review was submitted. `public-smoke.json`, `public-smoke-1.log` and `public-smoke-stdout.log` preserve the evidence.

`runtime-status.log`:production healthy; baseline4354/observed4358 after public smoke; same Hermes and healthyWeek06. `running-source-sha256.log` and `source-match.json` prove running guard/public entry/original harness/seller sources match local files. A real Docker restart/VPS reboot was not tested. Mock restart and state-loss cases were tested. Memory negotiation state cannot be migrated, and no seamless handoff is claimed.

**Original stop conditions remain:2026-10-21 09:00 KST OR cumulative observed MODEL_USAGE5000, whichever occurs first.** Afterward the guard exits0, so on-failure does not restart it. No extension was inferred from the migration request. This validates configuration and pre-deadline operation; it does not claim the future09:00 stop was already observed.

REPORT16,SETUP and existing three course notes were updated. Week07 challenge outcome aggregation and PR submission remain outside this migration result.
