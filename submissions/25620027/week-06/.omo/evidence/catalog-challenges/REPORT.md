# Catalog and challenge fixture evidence

Working directory: `/Users/sungjinho/Documents/Obsidian_Raw/jin_icloud2V2_raw/Vscode_Raw/20260529_cleaned_workspace/20_Study_Coursework/.work/ai-agent-engineering-101-week-06/submissions/25620027/week-06`.

Owned source: `market_a2a/catalog.py`, `generate_catalog.py`, `catalog.json`, `challenge_cases.json`, `shopping_list.json`, `tests/test_current_catalog.py`. No other implementation files were changed, no commits made, no API or seller/server calls executed by this lane.

## Exact validation invocations

All invocations run from the working directory above. Artifact paths below are relative to that directory.

```bash
/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python -m pytest tests/test_current_catalog.py -v -s --junitxml=.omo/evidence/catalog-challenges/pytest.xml > .omo/evidence/catalog-challenges/pytest.log 2>&1
/Users/sungjinho/.cache/uv/archive-v0/QANAKbvv7nwse57OPwm3B/bin/ruff check market_a2a/catalog.py generate_catalog.py tests/test_current_catalog.py > .omo/evidence/catalog-challenges/ruff.log 2>&1
PYTHONPATH=/Users/sungjinho/.cache/uv/archive-v0/VGu71qPFb53c3zS1PsS9v/lib/python3.12/site-packages /Users/sungjinho/.cache/uv/environments-v2/run-f252a28319e192a2/bin/python -m basedpyright market_a2a/catalog.py generate_catalog.py tests/test_current_catalog.py > .omo/evidence/catalog-challenges/types.log 2>&1
/Users/sungjinho/.cache/uv/environments-v2/run-f252a28319e192a2/bin/python /Users/sungjinho/.codex/plugins/cache/sisyphuslabs/omo/5.1.30/skills/programming/scripts/python/check-no-excuse-rules.py market_a2a/catalog.py generate_catalog.py tests/test_current_catalog.py > .omo/evidence/catalog-challenges/no-excuse.log 2>&1
shasum -a 256 catalog.json > .omo/evidence/catalog-challenges/generation.log
/Users/sungjinho/.cache/uv/environments-v2/run-f252a28319e192a2/bin/python generate_catalog.py
shasum -a 256 catalog.json >> .omo/evidence/catalog-challenges/generation.log
for file in market_a2a/catalog.py generate_catalog.py tests/test_current_catalog.py; do awk 'NF && !/^[[:space:]]*#/ {n++} END {print FILENAME, n}' "$file"; done > .omo/evidence/catalog-challenges/size.log
```

## Criteria and binary observables

| Criterion | Exact scenario under pytest invocation above | Binary observable | Captured artifact |
|---|---|---|---|
| Thousands of meaningful, unique, reproducible products | `test_catalog_is_reproducible_when_generated` | PASS: 3,008 products, distinct SKU/name/description, exactly five kinds, positive reserves, generated Product tuples equal committed data | `.omo/evidence/catalog-challenges/pytest.log`, `pytest.xml` |
| Byte-identical generator output | Generator and SHA-256 invocations above | Before/after SHA-256 both `f86e90098af0fd578da26745bdc65c5d045aa2b6997b39105d644395683b9327` | `.omo/evidence/catalog-challenges/generation.log` |
| Six descriptive requests find intended products without their names | `test_description_finds_expected_product_when_name_is_omitted`, cases `reading-light`, `low-bookcase`, `office-support`, `cable-workspace`, `secure-supplies`, `bookcase-disambiguation` | Six PASS results; every top candidate equals independent expected SKU; request does not contain target name; 1–6 candidates | `.omo/evidence/catalog-challenges/pytest.log`, `pytest.xml` |
| Five shopping targets remain findable in specialized, differently priced shops | `test_shopping_description_finds_product_in_specialist_shop`, five parametrized request IDs | Five PASS results; target ranks first; reserve equals original +20; all returned kinds match shop profile | `.omo/evidence/catalog-challenges/pytest.log`, `pytest.xml` |
| Shopping fixture has no duplicate request/product and negotiation headroom | `test_shopping_fits_total_budget_when_all_targets_are_selected` | PASS: five unique request IDs, five unique products, five kinds; actual reserve sum 1,250; 1.5× sum below budget 2,200 | `.omo/evidence/catalog-challenges/pytest.log`, `pytest.xml` |
| Baseline trade products remain compatible | `test_baseline_remains_first_when_only_item_is_named` | Three PASS results; first SKU/name `lamp`, `chair`, `bookshelf`, reserves 100/180/260 | `.omo/evidence/catalog-challenges/pytest.log`, `pytest.xml` |
| Explicit attributes constrain the complete returned shortlist | `test_constraints_are_hard_limits_when_generic_variants_compete` | PASS: all products wooden, brown, bookshelf, capacity ≥40, height ≤75 cm | `.omo/evidence/catalog-challenges/pytest.log`, `pytest.xml` |
| Empty/unrelated/impossible search does not fabricate results | `test_search_is_empty_when_no_product_satisfies_request` | Three PASS results return empty tuple | `.omo/evidence/catalog-challenges/pytest.log`, `pytest.xml` |
| Caller cannot dump whole catalog via large limit | `test_search_stays_bounded_when_caller_requests_everything` | PASS: requested limit 3,000 yields six records | `.omo/evidence/catalog-challenges/pytest.log`, `pytest.xml` |
| Missing products and malformed catalog data fail clearly | `test_get_raises_typed_error_when_sku_is_absent`, `test_catalog_rejects_duplicates_when_loading`, `test_catalog_rejects_invalid_records_when_loading` | Three PASS results: typed SKU error, duplicate boundary error, Pydantic validation failure | `.omo/evidence/catalog-challenges/pytest.log`, `pytest.xml` |
| Strict code quality and module size | Ruff, basedpyright, no-excuse, awk invocations above | `All checks passed!`; `0 errors, 0 warnings, 0 notes`; `no violations in 3 file(s)`; pure LOC 200/185/147 | `.omo/evidence/catalog-challenges/ruff.log`, `types.log`, `no-excuse.log`, `size.log` |

Final targeted suite: **24 passed**. Live negotiations and receipts are the parent agent's separate validation responsibility; this report claims retrieval/data behavior only.

## Integration contract

`Catalog(path, price_offset=0, kinds=None)` exposes immutable Product tuples, `.search(query, limit=6)` with a hard maximum of six, and `.get(sku)` raising `MissingProductError(sku)`. Search normalizes aliases, filters recognized colors/materials/kinds and common height/capacity expressions, then ranks overlap with inverse document-frequency weights. The model must evaluate the resulting short descriptions. An unspecified desk-height comparison is explicitly interpreted as 75 cm, also described in the target product.

Curated SKU/kind/reserve: `lamp-nocturne`/lamp/120, `chair-stratus`/chair/250, `bookshelf-meadow`/bookshelf/280, `desk-harbor`/desk/360, `storage-cove`/storage/240. The fixtures contain expected SKU oracles solely for verification; buyer/model inputs must contain only request ID and description.

## Environment and review

Initial `uv run --python 3.12.11 generate_catalog.py` could not access the default uv cache. A temporary uv cache then encountered unavailable PyPI DNS. Validation used existing Python 3.12.11 runtimes supplied by the parent; the generator retains PEP 723 metadata for a portable fresh invocation. No LSP installation was attempted.

Self-review: each source module has one responsibility (catalog retrieval, catalog generation, catalog behavior tests); file JSON is parsed into Product/Pydantic models; there is no tagged-union branching, untyped escape hatch, broad exception handler, network code, or one-off helper abstraction. The requested constructor necessarily takes its path plus two related shop profile options. Tests check observable result identities, constraints and generated data rather than implementation prose. All modules meet the ≤250 pure-line limit. The programming skill is provider-managed; no reusable user-owned skill change was warranted.
