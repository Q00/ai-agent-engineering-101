#!/bin/bash
# The harness model: Qwen3.8-27B on a local vLLM server (same model and snapshot as weeks 03-05).
# Both agents call it through harness.py (OpenAI-compatible chat API, thinking off).
export CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-5}
exec vllm serve Qwen/Qwen3.8-27B --revision 1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0 \
  --served-model-name Qwen/Qwen3.8-27B \
  --port 8011 --host 127.0.0.1 --max-model-len 8192 --gpu-memory-utilization ${GPU_UTIL:-0.90} \
  --limit-mm-per-prompt '{"image":0,"video":0}' --reasoning-parser qwen3
