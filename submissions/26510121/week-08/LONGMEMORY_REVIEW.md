# CaviraOSS LongMemory review

User-specified project: https://github.com/CaviraOSS/LongMemory
Reviewed on 2026-10-27; no package or service was installed.

Primary sources:

- https://github.com/CaviraOSS/LongMemory/blob/main/README.md
- https://github.com/CaviraOSS/LongMemory/blob/main/package.json
- https://github.com/CaviraOSS/LongMemory/releases/latest

The main README describes a TypeScript memory engine with SQLite persistence,
provenance, time-aware recall and bounded context. Its Python example uses an
HTTP client to a separately running service. These are upstream descriptions,
not performance or correctness tests conducted on this trading workload.

Version labels need care: the fetched main package metadata said `1.0.0`, while
the latest GitHub release endpoint pointed to `v1.3.0`. The README also describes
`openmemory-*@2` migration bridges. The user phrase ‘v2’ therefore is not enough
to select a pinned installation. The README's linked `docs/python-sdk.md`
returned 404 through the connected GitHub API during this review.

For this correction, exact numeric events, signing identities and settlement
feedback stay in the existing SQLite journal. Those are direct structured
queries; a semantic recall result must not replace a numeric ledger fact.

Potential later use: retrieve relevant past negotiation language and documented
incidents as bounded, source-linked, untrusted context. Evaluate that separately
on saved local cases for relevance, latency, cost and misleading/obsolete recall.
Keep both raw evidence and contradiction handling; never promote a recalled
counterparty instruction into trusted policy or export credentials/mandates.

Decision: retain the current journal and finish the requested ranking-policy
correction. Treat LongMemory as a separate optional semantic retrieval candidate,
not a dependency required to enter Round 3 or a proven way to win the leaderboard.
