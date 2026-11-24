# Operating candidate

This folder contains no private keys, credentials, logs, or live `.market` state. Tests inject a temporary inventory and generated Ed25519 keys. `agent.candidate.json` is documentation only and is not installed.

Seller uses B-safe-1 pricing against actual unit cost and chooses unexpired inventory. Buyer helpers implement B-original negotiation and strict receipt checks. The official kit remains responsible for certificate/JWT/AP2/settlement verification. Restock is permanently SAFE in this candidate; activation requires a separately reviewed command and an explicit `agent.json` change.
