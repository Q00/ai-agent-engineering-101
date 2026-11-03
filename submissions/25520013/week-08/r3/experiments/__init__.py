"""R3 meta-harness experiments (shadow; no decision-path module imports this package).

split.py    category-hash A/B split (sha256 of salt + category) and the catalog's category -> kind map
decide.py   difference-in-differences + stratified bootstrap 95% CI -> verdict compose | keep | rollback
prereg.py   preregistration records with a content hash; loss caps (2% per experiment, 5% total)
lessons.py  layer 5: append-only lessons, each with evidence ledger seqs, n and a CI checked against the ledger
state/      policy_base.json (baseline policy for experiments and tests), catalog.json (category universe)
"""
