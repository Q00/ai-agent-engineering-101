"""Edited from Week 02 starter — tools, model call, and meter shared by both harnesses.

Both harnesses import from here. Same tools and same model for both is what
makes the A/B a harness comparison and not a tool comparison.

Provider is picked from the environment:
  ANTHROPIC_API_KEY set          -> Anthropic SDK (pip install anthropic)
  otherwise                      -> OpenAI-compatible (pip install openai)
                                    OPENAI_API_KEY, optional OPENAI_BASE_URL
                                    (https://openrouter.ai/api/v1 for OpenRouter)
  AGENT_MODEL                    optional model override for either provider

Add Completion Client for CNP contractor
"""
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------- meter


class Meter:
    """The four metrics of the lab, counted in one place."""

    def __init__(self):
        self.tokens = 0
        self.iters = 0            # one iteration = one model call
        self.interventions = 0    # times a human approved or denied a call

    def add(self, input_tokens: int, output_tokens: int):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.iters += 1



PROVIDER = "openai"
MODEL = os.environ.get("AGENT_MODEL", "muse-spark-1.3")

_client = None


def _get_client():
    global _client
    if _client is None:
        if PROVIDER == "openai":
            key = os.environ.get("MODEL_API_KEY")
            if not key:
                raise RuntimeError("MODEL_API_KEY is not set")
            _client = OpenAI(
                base_url="https://api.meta.ai/v1",
                api_key=key,
            )
        else:
            import anthropic
            _client = anthropic.Anthropic()
    return _client


class Chat:

    def __init__(self, system: str, meter: Meter, tools: bool = True):
        self.system = system
        self.meter = meter
        self.tools = tools
        self.messages = []

    def complete(self, system: str, user: str):
        try:
            if PROVIDER == "anthropic":
                # block = {"type": "tool_result", "tool_use_id": call.id, "content": output}
                resp = _get_client().messages.create(
                    model = MODEL, max_tokens = 1024, system = system, messages = [{"role": "user", "content": user}]
                )
                self.meter.add(resp.usage.input_tokens, resp.usage.output_tokens)

                return "".join(b.text for b in resp.content if b.type == "text")
            
            resp = _get_client().chat.completions.create(
                model = MODEL,
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ]
            )
            usage = resp.usage
            self.meter.add(getattr(usage, "prompt_tokens", 0),
                       getattr(usage, "completion_tokens", 0))
            
            return (resp.choices[0].message.content or "")
        
        except Exception as e:
            raise RuntimeError(f"chat failed: {e}") from e
        
        