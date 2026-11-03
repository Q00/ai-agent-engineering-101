# Public seller implementation evidence

Working directory: submissions/25620027/week-06 in the assigned Week 06 worktree.
No active ulw-loop attempt was found. New evidence is isolated in this directory.

Interpreter/tool directory: `/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin`.

| Scenario | Exact invocation (from working directory) | Binary observable | Artifact |
| --- | --- | --- | --- |
| Public HTTPS Agent Card, browser GET and SDK POST coexist; invalid HTTP/credential/query URL rejected | `$BIN/python -m pytest tests/test_public_seller.py tests/test_current_seller.py -q` | PASS; card endpoint is https://seller.example/; HTML 200; unsupported RPC yields protocol error | pytest.txt |
| Concurrent pre-send request cap | Same pytest invocation; test_budget_when_concurrent_requests_exceed_allowance | PASS; 20 concurrent requests send exactly 7 upstream and block 13 | pytest.txt |
| Process restart persistence | Same pytest invocation; test_budget_when_process_restarts | PASS; subprocess charges allowance, parent process cannot consume again | pytest.txt |
| UTC day rollover; failed upstream request still charged | Same pytest invocation; test_budget_when_utc_day_changes and test_budget_when_upstream_fails | PASS; new day allows one; 503 retry never reaches upstream | pytest.txt |
| Existing seller commercial guards/protocol regression | Same pytest invocation; tests/test_current_seller.py | PASS; combined suite 19 passed | pytest.txt |
| Strict lint | `$BIN/ruff check public_seller.py tests/test_public_seller.py` | All checks passed | ruff.txt |
| Strict types using existing pyproject all mode | `$BIN/basedpyright --pythonpath $BIN/python public_seller.py tests/test_public_seller.py` | 0 errors, 0 warnings, 0 notes | basedpyright.txt |
| Programming structural rules | `$BIN/python /Users/sungjinho/.codex/plugins/cache/sisyphuslabs/omo/5.1.35/skills/programming/scripts/python/check-no-excuse-rules.py public_seller.py tests/test_public_seller.py` | no violations in 2 files | structure.txt |
| Existing locked dependency export | `uv export --script harness_app.py --frozen --offline --no-dev --cache-dir /tmp/ax06-public-uv-cache --output-file /tmp/week06-public-requirements.txt` | Successful hashed requirements export from existing lock; no additional lock introduced | export.txt |

`BIN` above is the exact interpreter/tool directory stated above; commands were executed with its expanded absolute path. Tests use no external API calls.

Owned production files: public_seller.py, deployment/Dockerfile, deployment/Dockerfile.dockerignore, deployment/compose.yaml. Owned test: tests/test_public_seller.py.

Architecture review: public_seller.py owns deployment adaptation (139 nonblank/noncomment lines); tests 122. Settings are parsed at the boundary, no tagged variants introduced, no Any/casts/ignore escapes or broad catches. Existing commercial harness/guards remain untouched. SQLite uses one atomic conditional UPSERT inside a transaction. Process state remains in memory; only model-request counter persists. Existing upstream connection retries occur before a billable request is sent; HTTP hooks charge redirects and model-level retries.

Deployment handoff, not yet claimed complete here: root executes actual image build, container healthcheck, Compose configuration, ingress/body/rate limits, public HTTPS and real transactions. Local Docker is unavailable. Static healthcheck command syntax parses. Container is one non-root Python process with a named persistent /data volume, host loopback port, CPU/memory bounds, and read-only rootfs; root must validate runtime behavior on VPS. Daily allowance is a request count, not a currency guarantee. No total-turn timeout was added; existing harness call/round limits and HTTP timeouts remain.

Skill reflection: no reusable skill defect observed; provider-managed skill was not modified.
