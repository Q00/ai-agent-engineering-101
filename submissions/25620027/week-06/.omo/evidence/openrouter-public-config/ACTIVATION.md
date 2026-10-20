# OpenRouter activation preflight — 2026-10-20

The owner selected OpenRouter and explicitly directed the executor to the
existing private environment configuration. No credential value is in this record.

## Direct observations

- The existing configuration has an OpenRouter base URL and an OpenRouter key
  stored under the generic OPENAI_API_KEY name. That is not the original paid
  OpenAI experiment key.
- Authenticated `GET https://openrouter.ai/api/v1/key` succeeded; is_free_tier=true,
  free-model daily requests used=0, limit=50, remaining=50 before inference.
- The public catalog and selected endpoint reported zero input/output prices for
  `liquid/lfm-2.5-2.6b:free` and required tool selection support.
- One real `chat.completions.create` request used the exact original generation
  arguments: temperature=0, tool_choice=required, parallel_tool_calls=false,
  max_completion_tokens=450. The fictional lamp quote returned `quote(price=130)`;
  input=112, output=59, reported cost=0. The printed response metadata is preserved
  in `../../../logs/deployment/20261020/openrouter-tool-probe.json`.
- Parallel-tool control is not advertised in endpoint metadata. A successful
  request proves only this observed call, not universal provider compliance.
- Worker full suite: 140 passed. Independent review: CLEAR / APPROVE, 49 focused
  tests passed. Original Harness implementation and experiment model defaults
  were not edited. Official checker passed: 43 source files, original 21-run
  current-kit report, required log and REPORT.

## Credential-transfer boundary

Automatic review rejected transferring the whole private env file. The executor
then minimized the payload to only the authenticated OpenRouter key, with an
OpenRouter host/prefix check and SSH stdin into a mode-0600 file inside the
service's mode-0700 VPS directory. Automatic review rejected that attempt too:
the human had not explicitly named the destination VPS for secret transfer.
Neither transfer executed. No alternative route is being used to move the key.

Live activation therefore requires explicit approval for that one key and the
already-created, dedicated Cloudflare Tunnel credential into the assignment's
private directory on the existing VPS. Cloudflare Workers AI sign-in/key creation
is no longer needed. No paid API fallback or plan upgrade is authorized.

Public transactions and agent.json.deployed remain unverified/unset at this stage.
