# R1 opening policy deployment

- Explicit user steering changed local-only work to immediate deployment, then R1 participation. Current-kit owner credential reuse was separately approved after automatic review rejected implicit credential access. Only that one value was reused in memory; no credential value is present in this evidence.
- Reviewed candidate source: `bf57c3c`. Live runtime source commit: `3dee810a8e382de8955faaa1ba54324ed2015f45`.
- All 26 tracked source/config/lock/test fingerprints matched the isolation snapshot before promotion. The only runtime source files promoted were seller_policy.py and seller_turn.py. Test and validation artifacts were copied afterward for reproducibility.
- Source SHA-256: seller_policy.py `b82bc6b463bff168ce3e24d621b6344f4241add06c1cdd0445daa27d67d8d631`; seller_turn.py `3ecf93712fcba63eeab3578b37fed3ba60cac581ddb7162694ac89a2daf3c4bf`.
- Independent offline suite 45/45, strict Ruff/types/format passed; reviewer APPROVE. Fresh actual-model official run 25/25. The pinned official test SHA-256 is `d610bc15448f5960d0b9b6bb9f181b38829bfb2ee863ab6c2a96abb9cb78f04d`.
- Existing kit stopped gracefully at 20:36:38 KST; improved kit started 20:36:39. Same seller and buyer name `25620027` registered in Round 1 at 20:36:41 and 20:36:42. Existing keys, buyer memory, fixed URL and tunnel were retained.
- Public Agent Card GET returned 200 with Sungjin Shop, JSONRPC 1.0 and `https://ax-week07.treasuresky.xyz/a2a/0`.
- First resumed buyer ledger #5373: bookshelf 270, stars 5, verified True, review accepted. These are buyer observations, not sales conversion evidence.
- New external seller task `b54c7859-7a82-45fc-a9a7-b071bb1e0833`: initial lamp quote 105 at 20:37:23; incoming 105 at 20:37:32; acceptance and receipt at 20:37:33. Server-side ledger verification and the received review were not inferred from the local receipt event.
- Before promotion, a point-in-time R1 board showed seller 1 deal / 4 visits / 1 review / 2 stars and buyer 12 deals / 14 orders / rank 2. This is not a before/after causal result.
- Supervisor exec session 85678, tunnel session 41396. Original continuous-run deadline 2026-10-20 20:59:21 KST, cumulative 120 purchase orders / 1000 observed model calls retained. At restart previous usage was 74 orders / 547 observed model calls. Authentication reset rejoin bounded to three.
- Candidate structural attempt 1 failed only because isolation intentionally had no real-market ledger log. After actual promotion, a labeled snapshot of the real live log was copied and both live and candidate structural checks passed. Failed and successful attempts are preserved.

Raw evidence: `logs/opening-policy-attempt-1/`, `logs/market-kit.log`, `logs/structural-opening-policy-attempt-1.log`, `logs/structural-opening-policy-attempt-2.log`. Earlier baseline remains in `logs/validation-attempt-1/`. No PR or push was performed in this change.
