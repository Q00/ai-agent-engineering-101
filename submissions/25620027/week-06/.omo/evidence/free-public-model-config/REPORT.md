# Public-only model configuration evidence

Working directory: `/Users/sungjinho/Documents/Obsidian_Raw/jin_icloud2V2_raw/Vscode_Raw/20260529_cleaned_workspace/20_Study_Coursework/.work/ai-agent-engineering-101-week-06/submissions/25620027/week-06`.
Scope: public_seller.py, market_a2a/harness.py, deployment/compose.yaml,
tests/test_public_seller.py, tests/test_harness_model.py.
No active ULW attempt was present. No Git mutations, credentials, cloud resources,
assignment reports or other agents' files were changed by this executor.

## Exact invocations and observables

- pytest: `PYTHONDONTWRITEBYTECODE=1 /Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python -m pytest -p no:cacheprovider -v > .omo/evidence/free-public-model-config/pytest.txt 2>&1`
- ruff: `/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/ruff check --no-cache . > .omo/evidence/free-public-model-config/ruff.txt 2>&1`
- format: `/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/ruff format --check --exclude .omo . > .omo/evidence/free-public-model-config/format-source.txt 2>&1`
- types: `/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/basedpyright --pythonpath /Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python > .omo/evidence/free-public-model-config/types.txt 2>&1`


All four commands exited 0 on the final run.
- pytest.txt: **117 passed**; verbose output records individual scenario identifiers.
- ruff.txt: **All checks passed!**
- format-source.txt: **49 files already formatted**.
- types.txt: **0 errors, 0 warnings, 0 notes**.

## Criteria and scenarios

| Criterion | Exact scenario (pytest invocation above) | Binary observable | Artifact |
|---|---|---|---|
| Original model retained; override honored | tests/test_harness_model.py::test_model_request_when_default_or_public_override[None] and [@cf/zai-org/glm-4.7-flash] | Outgoing serialized model equals original fixed model or distinct override; usage log agrees | pytest.txt |
| Public-only endpoint/token/model; no ambient paid settings or redirects | tests/test_harness_model.py::test_public_serve_when_paid_environment_is_present | One request at explicit CF endpoint, public Bearer token, no ambient org/project; 302 fails without second request | pytest.txt |
| Arbitrary ambient credential headers rejected | tests/test_harness_model.py::test_public_serve_when_custom_headers_are_present (2 cases) | ValueError before HTTP client factory can run; secret header text absent from error | pytest.txt |
| Missing/invalid config fails closed | tests/test_public_seller.py::test_settings_when_public_model_config_is_missing (3 cases) and ::test_settings_when_public_model_config_is_invalid (8 cases) | ValidationError for each malformed/missing model field | pytest.txt |
| Credential-safe error rendering | tests/test_public_seller.py::test_settings_error_when_raw_token_is_present | Raw token absent from ValidationError string and Settings repr | pytest.txt |
| Existing seller/discovery/budget behavior retained | full pytest suite, including test_public_discovery_when_configured and all DailyBudget scenarios | 117 tests pass | pytest.txt |

The ambient Authorization regression was directly reproduced before the final fix:
`python -m pytest -p no:cacheprovider -q tests/test_harness_model.py::test_public_serve_when_paid_environment_is_present`
exited 1 and observed combined public and paid synthetic credentials.
Captured at header-regression-before.txt. Final behavior rejects all nonempty
OPENAI_CUSTOM_HEADERS rather than retaining unknown credential-bearing headers.

## Checks with inherited limitations

- `ruff format --check .` initially included archived Markdown code fences:
  format.txt records two unformatted existing evidence documents. The final
  source-only command excludes .omo; those documents were preserved.
- Programming skill checker invocation:
  `PYTHONDONTWRITEBYTECODE=1 /Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python /Users/sungjinho/.codex/plugins/cache/sisyphuslabs/omo/5.1.35/skills/programming/scripts/python/check-no-excuse-rules.py market_a2a/harness.py public_seller.py tests/test_public_seller.py tests/test_harness_model.py`.
  It exits 1 with two preexisting match/assert_never warnings (audit.txt).
  Both match ASTs are byte-for-byte equal as AST dumps to HEAD, verified in
  scope-and-audit.txt. Existing assert_never calls follow the matches; strict
  basedpyright accepts both. This audit is not claimed clean.
- Local Docker executable unavailable; Compose container startup and real CF API
  calls were not performed. Free-tier account enforcement is a deployment concern,
  not inferred from a model-name prefix. No live-provider success claimed.

## Programming review

Responsibilities remain model loop, public runtime, deployment, and their tests.
Environment inputs are parsed to typed Settings; no new variant dispatch, type
escape hatch, broad catch, redundant validation, or one-off helper was introduced.
The new optional constructor parameter is the third business parameter.
All edited files are below 200 pure lines (scope-and-audit.txt).
Regression coverage distinguishes original/default from the public override.
Logging remains the existing emit path with the actual chosen model.
Skill retrospective: no reusable user-owned skill change identified; provider
managed skill files preserved.

