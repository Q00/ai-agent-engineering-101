# OpenRouter public configuration code review

codeQualityStatus: CLEAR
recommendation: APPROVE
blockers: []

## Scope and evidence

Reviewed the full working-tree diff and current contents of public_seller.py, deployment/compose.yaml, tests/test_public_seller.py, and tests/test_harness_model.py. Goal: isolate public model calls to the exact OpenRouter endpoint and explicit fixed :free models, retain paid experiment defaults, credential isolation, redirect rejection, and bounded outbound requests. No implementation edits were made. Unrelated RECORD_VERIFICATION.md changes were not reviewed or changed.

`omo-agent-toolkit ulw-loop status --json` returned ULW_LOOP_PLAN_MISSING, so this report uses the prescribed .omo/evidence/<goal>-code-review.md fallback. No notepad path was supplied. Inspected .omo/evidence/openrouter-public-config/REPORT.md and its pytest.txt, ruff.txt, basedpyright.txt, compose-ruby.txt, and skill-audit.txt artifacts. The saved suite shows 140 passed; independently reran the two scoped test modules with the existing Python runtime, bytecode disabled, and pytest cache disabled: **49 passed in 1.81s**, exit 0. Command: `PYTHONDONTWRITEBYTECODE=1 /Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python -m pytest tests/test_public_seller.py tests/test_harness_model.py -q -p no:cacheprovider`.

## Findings by severity

- CRITICAL: none.
- HIGH: none.
- MEDIUM: none.
- LOW: none.

## Correctness and regression assessment

The endpoint constraint admits only https://openrouter.ai/api/v1 with an optional trailing slash. The model constraint rejects missing :free suffixes, additional suffixes, query extensions, and malformed identifiers; the namespace validator additionally excludes openrouter routers. Unknown Settings fields fail closed. These checks sit at the deployment configuration boundary, where they are necessary for the stated goal.

SecretStr and hide_input_in_errors remain enabled. Public client creation still rejects ambient custom headers before constructing a client, supplies explicit credentials/base URL and empty organization/project values, disables retries, and disables redirects before sending. HTTP transport tests exercise these properties with conflicting synthetic paid environment settings. The actual SDK serializes the unchanged paid default separately from the public override. Git status confirms no Harness implementation modification in this change set.

The default budget is consistently 50 in Settings, CLI parsing, and Compose. The added budget test observes 50 sends and a blocked 51st send through the actual request hook, rather than merely asserting a configuration constant. Existing persistence, concurrent reservation, rollover, and failure accounting tests remain intact.

## Skill-perspective check

Loaded and consulted remove-ai-slops and programming SKILL.md files from the installed omo/5.1.35 plugin. Applied the overfit/slop pass to changed production code and tests. No deletion-only tests, removal-verification tests, tautologies, prompt prose assertions, new untyped escape hatches, needless abstractions, or unnecessary production extraction/normalization were found. Adversarial configuration inputs exercise a genuine boundary contract; the SDK/HTTP tests observe serialized requests and blocked redirects. No violation of either skill perspective was identified in this diff. Skill reflection found no reusable change to make; provider-managed skills were not edited.

## Limits

No credentials were read and no external model or deployment calls were made. This review verifies local configuration behavior, not continued availability of a free model or a full buyer/seller transaction. The parent-reported single OpenRouter probe was not independently inspected and is not relied upon for approval. Stored static-check evidence was inspected, not independently rerun. Approval applies only to the four-file configuration change.
