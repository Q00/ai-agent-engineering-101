#!/bin/bash
# Local model server for both agents. vLLM 0.17.1, same model and snapshot as weeks 03-04.
# Tool calling: the model's chat template emits <tool_call><function=...> XML, so the
# qwen3_coder parser turns it into OpenAI tool_calls. Thinking is off on the client side.
# GPU 0 was shared with another process (23 GB) during the runs, hence 0.68; with that
# little KV cache, CUDA-graph capture needs --max-num-seqs 16 (the runner sends one request at a time).
export CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-0}
exec vllm serve Qwen/Qwen3.8-27B --revision 1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0 \
  --served-model-name Qwen/Qwen3.8-27B \
  --port 8011 --host 127.0.0.1 --max-model-len 8192 --gpu-memory-utilization ${GPU_UTIL:-0.68} \
  --limit-mm-per-prompt '{"image":0,"video":0}' --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder --max-num-seqs 16
