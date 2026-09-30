from __future__ import annotations



import os
os.environ["JAX_PLATFORMS"] = "cpu"
os.environ["XLA_PYTHON_CLIENT_PREALLOCATE"] = "false"

import json
import re 

import asyncio
from typing import Any

try:
    import jax.numpy as jnp
    HAS_JAX = False             # blocked due to Segment  fault (core dumped)
except Exception:
    jnp = None
    HAS_JAX = False


def _jax_mul(a: float, b: float) ->  float:
    x = jnp.array(a)
    y = jnp.array(b)

    return float(x * y)

def _jax_mean(xs: list[float]) -> float:
    return float(jnp.mean(jnp.array(xs)))

def run_compute(desc: str) -> tuple(str, bool, str):
    mul = re.search(r"(\d+)\s*\*\s*(\d+)", desc)
    if mul:
        if not HAS_JAX:
            return str(int(mul.group(1)) * int(mul.group(2))), True, "numpy-fallback"
        val = _jax_mul(float(mul.group(1)), float(mul.group(2)))

        return str(int(val)), True, "jax"
    

    nums = [float(x) for x in re.findall(r"\d+\.\d", desc)]

    if "mean" in desc.lower() and nums:
        if not HAS_JAX:
            return str(sum(nums) / len(nums)), True, "numpy-fallback"

        return str(_jax_mean(nums)), True, "jax"
    
    return "", False, "unparsed-compute"

def _score_execution(task: dict, output: str) ->  bool:
    kind = task.get("kind")

    if kind == "compute":
        expected = str(task.get("expected", "")).strip()
        compact = output.strip().split()[0] if output.strip() else ""
        if expected and (expected in output or compact.startswith(expected)):
            return True
        
        try:
            return abs(float(compact) - float(expected)) < 1e-6
        except Exception:
            return False
        
    if kind == "code":
        return "def" in output and "return" in output
    if kind == "write":
        return len(output.split()) >= 12
    
    return bool(output.strip())

EXEC_SYSTEM = (
    "You are contractor {name}. You won the contract. "
    "Perform the task. Return only the answer or source code. No preamble."
)

async def execute_task(name: str, task: dict, chat) ->  dict[str, Any]:
    kind = task.get("kind", "")
    desc = task["desc"]

    if kind == "compute":
        output, ok_parse, backend = await asyncio.to_thread(run_compute, desc)
        
        
        success = ok_parse and _score_execution(task, output)
        
        return {
            "contractor": name,
            "kind": kind,
            "backend": backend,
            "output": output,
            "success": success,
        }
    
    """def _llm() -> str:
        return chat.complete(EXEC_SYSTEM.format(name=name), desc)
    
    
    output = await asyncio.to_thread(_llm)"""
    output = await chat.complete(EXEC_SYSTEM.format(name=name), desc)


    return {
        "contractor": name,
        "kind": kind,
        "backend": "llm",
        "output": output,
        "success": _score_execution(task, output),
    }