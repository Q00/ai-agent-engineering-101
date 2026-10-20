# OpenRouter public configuration evidence

Working directory: `submissions/25620027/week-06` in the assigned worktree.
Runtime directory: `/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin`.
No active ulw-loop attempt was found; evidence is stored in this assigned directory.

Changed only `public_seller.py`, `deployment/compose.yaml`, `tests/test_public_seller.py`, and `tests/test_harness_model.py`, plus this evidence directory. The harness implementation and generation arguments were not edited. No credentials, cloud model calls, Git operations, or deployment operations were accessed or performed by this worker.

## Behavioral validation

Invocation (from working directory):

```sh
/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python -m pytest tests -v --junitxml=.omo/evidence/openrouter-public-config/pytest.xml
```

Observed: exit 0, **140 passed**, zero failures/skips. Captured artifacts: `pytest.txt`, `pytest.xml` in this directory. HTTP boundary tests use the actual OpenAI serializer and an in-memory HTTP transport; they do not claim remote-provider availability.

| Criterion | Exact scenario / pytest node | Binary observable |
|---|---|---|
| Exact OpenRouter endpoint, optional trailing slash | `test_public_serve_when_paid_environment_is_present[]` and `[/]` | Exactly one outgoing request reaches `https://openrouter.ai/api/v1/chat/completions` |
| Fixed free model and explicit public token | Same two public service scenarios | Serialized model equals `liquid/lfm-2.5-2.6b:free`; Authorization equals synthetic public token |
| No paid ambient routing/credential leakage | Same two public service scenarios | Paid ambient base/key/org/project are not used; org/project headers are empty |
| Redirect blocking | Same two public service scenarios | A 302 to OpenAI raises APIStatusError; transport observes exactly one request |
| Original paid model default remains separate | `test_model_request_when_default_or_public_override[None]` | Actual SDK body and usage log name `gpt-4.1-nano-2025-04-14` |
| Explicit free model override | `test_model_request_when_default_or_public_override[liquid/lfm-2.5-2.6b:free]` | Actual SDK body and usage log name the explicit free model |
| Reject endpoint/model/key violations | All `test_settings_when_public_model_config_is_invalid` cases | ValidationError for OpenAI/Cloudflare, HTTP, deceptive host, URL credentials, explicit port, query, fragment, extra path, newline; paid model, missing suffix, auto/free routers, missing namespace/model, plugin/query suffix, invalid key |
| Require public configuration | All `test_settings_when_public_model_config_is_missing` cases | Missing endpoint/model/key raises ValidationError |
| Reject extra routing options | `test_settings_when_extra_routing_fields_are_present[plugins]`, `[provider]`, `[query]` | Unknown options raise ValidationError |
| Reject ambient custom headers before HTTP creation | Both `test_public_serve_when_custom_headers_are_present` cases | ValueError occurs before client factory is called; error omits header value |
| Secret masking / validation-error hiding | `test_settings_error_when_raw_token_is_present` | Raw token absent from rendered exception; configured token absent from settings repr |
| Public default budget of 50 | `test_default_budget_when_fifty_requests_are_sent` | Exactly 50 outbound sends; request 51 raises BudgetExhaustedError before transport |
| Existing budget protections | `test_budget_when_concurrent_requests_exceed_allowance`, `test_budget_when_process_restarts`, `test_budget_when_utc_day_changes`, `test_budget_when_upstream_fails` | Concurrent cap exact, persisted exhaustion survives process restart, UTC reset works, failed request consumes allowance |
| Existing public discovery and protocol guards | `test_public_discovery_when_configured`, `test_public_root_when_browser_or_rpc_client_connects`, all invalid-public-URL cases | HTTPS discovery card, HTML root, RPC errors, and URL validation remain passing |
| Remaining existing offline behavior | Full invocation above | All 140 cases pass |

## Static validation

All commands below exited 0 and captured the named artifact in this directory:

```sh
/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/ruff check public_seller.py tests/test_public_seller.py tests/test_harness_model.py
# ruff.txt: All checks passed!
/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/ruff format --check public_seller.py tests/test_public_seller.py tests/test_harness_model.py
# format.txt: 3 files already formatted
/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/basedpyright --pythonpath /Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python public_seller.py tests/test_public_seller.py tests/test_harness_model.py
# basedpyright.txt: 0 errors, 0 warnings, 0 notes
/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python /Users/sungjinho/.codex/plugins/cache/sisyphuslabs/omo/5.1.35/skills/programming/scripts/python/check-no-excuse-rules.py public_seller.py tests/test_public_seller.py tests/test_harness_model.py
# skill-audit.txt: no violations in 3 file(s)
```

Compose scenario: parse YAML, require explicit public model settings, check the 50-request default, and reject ambient OpenAI environment entries. Exact passing invocation:

```sh
/usr/bin/ruby -ryaml -e 'config = YAML.load_file("deployment/compose.yaml"); env = config.fetch("services").fetch("seller").fetch("environment"); abort "wrong budget" unless env.fetch("AX_DAILY_MODEL_REQUESTS") == "${AX_DAILY_MODEL_REQUESTS:-50}"; abort "ambient settings" unless %w[OPENAI_API_KEY OPENAI_BASE_URL OPENAI_ORG_ID OPENAI_PROJECT_ID OPENAI_CUSTOM_HEADERS].none? { |name| env.key?(name) }; abort "optional public setting" unless %w[AX_PUBLIC_MODEL_API_KEY AX_PUBLIC_MODEL_BASE_URL AX_PUBLIC_MODEL].all? { |name| env.fetch(name).start_with?("${#{name}:?") }; puts "PASS: compose parses; public-only model settings are mandatory; daily default is 50; ambient OpenAI settings are absent"'
```

Observed: exit 0, PASS; artifact `compose-ruby.txt`. Earlier Python YAML validation could not start because PyYAML is absent (`compose.txt`); the full same scenario was rerun using the installed Ruby YAML parser above. No packages were installed.

Skill reflection: existing typed settings and HTTP-boundary tests fit this narrow change. No reusable skill change was needed; the plugin-managed skill was not modified.
