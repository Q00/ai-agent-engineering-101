# VPS package local verification

Assignment scope: only `week-07/deploy/vps/`; no remote deployment, model request,
key/env/log edits, installation or Git commit. No current ulw-loop attempt exists
(`omo-agent-toolkit ulw-loop status --json` returned ULW_LOOP_PLAN_MISSING).
Evidence is therefore in this package's `.omo/evidence/`.

## Guard scenarios (11/11 PASS)

Invocation from week-07:

```sh
VPS_TEST_EVIDENCE=deploy/vps/.omo/evidence/guard-reviewed python3 deploy/vps/test_guard.py
```

Full captured unittest output: `guard-reviewed-tests.txt`.
The below artifact paths are relative to `guard-reviewed/`.

| Exact scenario | Binary observable | Artifact |
|---|---|---|
| test_cap_and_restart_keeps_cumulative_baseline | First run reaches 5000 and exits 0; next run exits 0 with unchanged child log | test_cap_and_restart_keeps_cumulative_baseline/observations.jsonl |
| test_existing_persistent_usage_counts_on_restart | Prior remote record + baseline + new record = 5000; prior log retained | test_existing_persistent_usage_counts_on_restart/observations.jsonl; data/seller.log within that scenario |
| test_absolute_deadline_prevents_child_launch | Fixed deadline constant equals requested timestamp; elapsed deadline exits 0 and creates no seller log | test_absolute_deadline_prevents_child_launch/observations.jsonl |
| test_deadline_stops_silent_children | Silent child pair stops on deadline in under 3 seconds, exit 0 | test_deadline_stops_silent_children/observations.jsonl |
| test_child_failure_returns_nonzero_for_bounded_restart | Child exit 7 yields guard exit 1 and retains output | test_child_failure_returns_nonzero_for_bounded_restart/observations.jsonl |
| test_sigterm_stops_both_children_normally | SIGTERM yields exit 0/signal with both started child logs retained | test_sigterm_stops_both_children_normally/observations.jsonl |
| test_replaced_baseline_fails_closed | Changed baseline raises before child launch | test_replaced_baseline_fails_closed/rejection.txt |
| test_lost_baseline_fails_closed | Missing baseline beside existing logs raises before launch | test_lost_baseline_fails_closed/rejection.txt |
| test_deleted_log_cannot_reduce_persistent_count | Previous state count greater than available log count raises before launch | test_deleted_log_cannot_reduce_persistent_count/rejection.txt |
| test_truncated_log_cannot_reduce_persistent_count | Truncated log cannot lower saved count; raises before launch | test_truncated_log_cannot_reduce_persistent_count/rejection.txt |
| test_staging_disables_only_tunnel | AX_TUNNEL_ENABLED=0 leaves only public seller command | test_staging_disables_only_tunnel/commands.json |

These are real local subprocesses producing synthetic usage records. They do not
contact a model. Earlier 8- and 9-scenario runs remain separately recorded.

## Public wrapper (PASS)

Invocation: `.venv/bin/python deploy/vps/test_public.py` from week-07.
Observable: actual wrapper creates a loopback port-8900 server configuration;
actual app card is version 1.2.0 and advertises the existing public HTTPS URL;
card, homepage, comparison JSON and JS each return HTTP 200; comparison is enabled.
Artifacts: `public-tests.txt`, `public-card.json`, `public-routes.json`.
This uses ASGI in-process requests, an ephemeral in-memory signer and replaced
server/model boundary. No remote call, paid model call or stored-key validation.

## Package/config checks (PASS within stated local scope)

- `UV_CACHE_DIR=/tmp/week07-vps-uv-cache uv export --frozen --offline --script harness_app.py --format requirements.txt --output-file deploy/vps/.omo/evidence/locked-requirements.txt`
  succeeded without installing dependencies or changing the original lock.
  Observable: nonempty pinned requirements with hashes. Artifact:
  `locked-requirements.txt`. Initial default-cache permission error is preserved
  only as earlier execution history; retry used the allowed temporary cache.
- `ruby -r yaml -r json -e 'd=YAML.load_file(ARGV[0]); s=d["services"]["seller"]; abort "published ports" if s.key?("ports"); abort "bad restart" unless s["restart"]=="on-failure:3"; abort "bad limits" unless s["cpus"]==1.0 && s["mem_limit"]=="768m" && s["pids_limit"]==128; puts JSON.pretty_generate({"yaml_parse"=>"PASS", "ports_absent"=>true, "restart"=>s["restart"], "cpus"=>s["cpus"], "memory"=>s["mem_limit"], "pids"=>s["pids_limit"]})' deploy/vps/compose.yml`
  Observable: YAML parses; no ports; requested limits/restart match.
  Artifact: `compose-static.json`.
- Python `ast.parse(path.read_text(), filename=str(path))` for guard, public
  wrapper and both local test scripts succeeded. Artifact: `syntax.json`.
- Final source hashes: `source-sha256.json`.

Docker is unavailable locally, so no local Docker image build, Docker Compose
semantic validation, real tunnel, actual private signing-key load or VPS claim is
made here. The root cutover operator owns those checks. All use of MODEL_USAGE
is an observed-response stop policy; already-in-flight responses may finish.
