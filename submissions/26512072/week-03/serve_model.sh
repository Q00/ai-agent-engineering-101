#!/bin/bash
# Local model server used for the runs. vLLM 0.17.1, one A100 80GB.
# Reasoning parser is on so any thinking text is separated from the reply;
# the client also sends enable_thinking=false (see llm.py).
export CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-0}
exec vllm serve Qwen/Qwen3.8-27B --served-model-name Qwen/Qwen3.8-27B \
  --port 8011 --host 127.0.0.1 --max-model-len 8192 --gpu-memory-utilization 0.92 \
  --limit-mm-per-prompt '{"image":0,"video":0}' --reasoning-parser qwen3
