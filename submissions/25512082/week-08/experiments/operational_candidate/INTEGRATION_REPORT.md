# Local v4 integration result

This candidate is not installed in `agent.json` and cannot join a market. No real key, claim code, logs, or `.market` files are present.

## Connected policy

- Seller: B-safe-1, using actual unit cost, 5% cost floor, 82% list-price target, unexpired real-unit selection, three-turn limit, and signed receipt containing the selected unit.
- Buyer: B-original deterministic price path, budget/SKU/final-price/signature-presence guards, and A2A v1 JSON-RPC messages.
- Restock: exact `{"buy":[]}` only. The dry-run policy remains outside the candidate command and cannot purchase.

## Real local HTTP A2A E2E

The candidate Seller was launched as a real uvicorn child process bound only to an ephemeral `127.0.0.1` port. The Buyer fetched its Agent Card, sent A2A v1 JSON-RPC `SendMessage` requests over HTTP, received a quote, sent a B-original offer, received a signed receipt, and verified it through the official kit. The process was terminated, restarted with the same ledger, and refused reuse of the already agreed unit.

The first E2E run exposed a real compatibility issue: A2A serialized integer price `113` as `113.0`. The Buyer initially rejected it. Integral floats are now normalized to integers exactly as kit v4 does; non-integral or over-budget prices remain rejected.

## Code paths actually exercised

- A2A: live 127.0.0.1 uvicorn Seller plus candidate Buyer Agent Card parsing and `SendMessage` JSON-RPC request/response. Separate unit tests still use `httpx.MockTransport` for deterministic malformed-response cases.
- Receipt: generated test-key Ed25519 signature checked through official `market_kit.verify_seller`; changed price rejected.
- ACP: official `market_kit.AcpShop.create` selected the injected inventory unit, issued a checkout session, rejected duplicate task checkout, and rejected a changed receipt signature.
- Certificate/accept: official `market_kit.check_accept` verified a certificate signed by a generated test market key and enforced grade/shelf-life conditions. This proves the verifier path, not possession of a real market certificate.
- AP2-shaped completion: official `AcpShop.handle` forwarded checkout JWT, order, checkout mandate, and payment mandate to `FakeMarket.call`. FakeMarket returned a local mandate receipt. Market-side mandate signatures, ownership, funds, and settlement were not proven.
- AP2 errors: FakeMarket rejected amount mismatch, open-budget excess, and same-owner settlement with 402; official ACP surfaced those failures. Inventory server error and duplicate completion were also rejected. A timeout and a malformed 200 response were not silently accepted, but official `AcpShop.handle` propagates exceptions in these synthetic cases; production HTTP middleware behavior still needs official-environment confirmation.
- Persistence: an atomic JSON ledger records transaction/order/unit/status/rejection reason. Restart reload and duplicate-unit refusal passed. Injected `os.replace` failure preserved the previous complete ledger.
- Data: sanitized official-shaped catalog, inventory, demand, order, and shops fixtures passed SKU/unit/cost/list-price/grade/expiry/acceptable/budget checks.
- Restock: exact SAFE output remained empty. Dry-run proposed two test goods units at estimated total 200, within 10% per-call and 20% held-stock Mock limits, while leaving `buy` empty and balance unchanged.

## Still required

Replace candidate placeholders only after review, connect durable payment/history IDs, run the official instructor-provided test market if available, inspect real catalog/inventory/demand shapes, and approve a restock dry-run. Keep SAFE and the existing `agent.json` until then.
