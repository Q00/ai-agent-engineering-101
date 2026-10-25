#!/bin/bash
# Local model server used for the runs. vLLM 0.17.1, one RTX PRO 6000 Blackwell 96GB.
# Same model, snapshot, and flags as week 03. Reasoning parser is on so any
# thinking text is separated from the reply; the client also sends
# enable_thinking=false (see llm.py).
export CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-0}
exec vllm serve Qwen/Qwen3.8-27B --revision 1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0 \
  --served-model-name Qwen/Qwen3.8-27B \
  --port 8011 --host 127.0.0.1 --max-model-len 8192 --gpu-memory-utilization 0.92 \
  --limit-mm-per-prompt '{"image":0,"video":0}' --reasoning-parser qwen3
