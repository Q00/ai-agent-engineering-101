"""One seller or buyer decision: route by the harness, call the model or a sandboxed script, enforce the rules
(human code, not editable by the model).

The harness is reloaded on every call so the operator's edits apply at once. Any failure falls back to the harness's
fallback_script once, then to the do-nothing action. Exactly one trajectory row of kind "decision" per call; it holds
a summary and hash of the situation, never its text.

When the harness memory.json has top_k > 0, a model-routed call first asks the mem0 daemon (kernel.memory) with a
mechanical query (decision kind, SKUs, categories, hashed shop ids; no strategy) inside a ~1 s budget. A dead daemon
only means no memories.
"""

import copy
import hashlib
import json
import os
import time

from kernel import common as c
from kernel import harness as hz
from kernel import memory, rules, sandbox
from kernel.llm import ModelError, ModelRefused

EFFORTS = ("low", "medium", "high")
MEM_BUDGET_S = 1.0
MAX_MEM_KEYS = 12
SWAP_RETRY_S = 0.05


class ParseError(ValueError):
    """No JSON object in the model output."""


def parse_last_object(text):
    """Last top-level {...} JSON object in text (prose and code fences around it are ignored)."""
    dec = json.JSONDecoder()
    found, i = None, 0
    while True:
        i = text.find("{", i)
        if i < 0:
            break
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict):
            found = obj
        i = end
    if found is None:
        raise ParseError("no JSON object in output")
    return found


def situation_summary(situation, text):
    keys = {}
    for k, v in (situation or {}).items():
        keys[k] = len(v) if isinstance(v, (list, dict, str)) else type(v).__name__
    return {
        "keys": keys,
        "bytes": len(text.encode("utf-8")),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def memory_keys(situation, ctx):
    """Mechanical lookup keys: SKUs and categories found in the situation, hashed urls of ctx shop ids."""
    keys = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("sku", "category") and isinstance(v, str) and v:
                    keys.append(v)
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(situation)
    keys += [c.hid(u) for u in (ctx.get("shop_ids") or {}).values() if u]
    return list(dict.fromkeys(keys))[:MAX_MEM_KEYS]


class Agent:
    def __init__(
        self,
        name,
        harness_dir,
        terra,
        traj,
        market_brief_path,
        isolate=True,
        mem_sock=None,
    ):
        self.name, self.harness_dir, self.terra, self.traj = (
            name,
            harness_dir,
            terra,
            traj,
        )
        self.market_brief_path, self.isolate = market_brief_path, isolate
        self.mem_sock = mem_sock

    def _brief(self):
        try:
            with open(self.market_brief_path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def _memories(self, h, kind, keys):
        k = memory.bound_top_k((h.memory_cfg or {}).get("top_k"))
        if not k:
            return []
        query = c.mask(" ".join([kind] + keys), self.traj.secrets)
        sock = self.mem_sock or memory.sock_path()
        return memory.search(sock, self.name, query, k, timeout=MEM_BUDGET_S)

    def _prompt(self, h, kind, situation_json, memories=()):
        mem = []
        if memories:
            mem = [
                "## 기억 (mem0, 관련도순)",
                "\n".join(f"- {c.mask(m, self.traj.secrets)}" for m in memories),
            ]
        return "\n\n".join(
            [
                h.always_text,
                self._brief(),
                *mem,
                "## 지금 할 결정",
                kind,
                rules.SCHEMAS.get(kind, ""),
                "## 상황",
                situation_json,
                "Answer with exactly one JSON object.",
            ]
        )

    def _script(self, name, kind, situation, ctx, timeout_s):
        scripts = os.path.join(self.harness_dir, "scripts")
        return sandbox.run_script(
            os.path.join(scripts, name),
            {"kind": kind, "situation": situation, "ctx": ctx},
            timeout_s,
            isolate=self.isolate,
            allowed_dir=scripts,
        )

    @staticmethod
    def _translate(kind, raw, ctx):
        """Map the model's short shop ids (ctx["shop_ids"]) back to urls before the rule check."""
        ids = ctx.get("shop_ids") or {}
        if (
            kind == "buyer.plan"
            and ids
            and isinstance(raw, dict)
            and isinstance(raw.get("candidates"), list)
        ):
            raw = dict(raw)
            raw["candidates"] = [
                dict(x, shop=ids.get(x.get("shop"), x.get("shop")))
                if isinstance(x, dict)
                else x
                for x in raw["candidates"]
            ]
        return raw

    def decide(self, kind, situation, ctx):
        t0 = time.monotonic()
        errors, version, route_used, decision = [], None, "safe", None
        keys, memories = memory_keys(situation, ctx), []
        situation_json = c.mask(
            json.dumps(situation, ensure_ascii=False, sort_keys=True), self.traj.secrets
        )

        h, bad = None, []
        arm = ctx.get("arm") if isinstance(ctx, dict) else None
        trial_dir = ctx.get("harness_dir") if isinstance(ctx, dict) else None
        if isinstance(ctx, dict) and (arm or trial_dir):  # kernel routing only; scripts and rules never see it
            ctx = {k: v for k, v in ctx.items() if k not in ("arm", "harness_dir")}
        if trial_dir:  # a side-by-side trial candidate: a stored library copy, never swapped while in use
            try:
                bad = hz.validate_dir(trial_dir)
                if not bad:
                    h = hz.load(trial_dir)
                    version = h.version
            except (OSError, ValueError) as e:
                bad = [str(e)]
            if h is None:  # a broken candidate must not cost the sale: the current version answers
                errors += [f"trial harness: {e}" for e in bad]
        for attempt in range(2 if h is None else 0):  # versions._install swaps the dir with two renames; it is briefly absent
            try:
                bad = hz.validate_dir(self.harness_dir)
                if not bad:
                    h = hz.load(self.harness_dir)
                    version = h.version
            except (OSError, ValueError) as e:
                bad = [str(e)]
            if h is not None or attempt:
                break
            time.sleep(SWAP_RETRY_S)
        if h is None:
            errors += [f"harness: {e}" for e in bad]

        if h is not None:
            route = h.routing.get(kind) or {"by": "model"}
            effort = route.get("effort", "medium")
            if effort not in EFFORTS:
                effort = "medium"
            timeout_s = route.get("timeout_s", 60)
            if (
                not isinstance(timeout_s, (int, float))
                or isinstance(timeout_s, bool)
                or timeout_s <= 0
            ):
                timeout_s = 60
            by = route.get("by", "model")
            try:
                if by == "script":
                    raw = self._script(
                        route.get("script"), kind, situation, ctx, timeout_s
                    )
                else:
                    memories = self._memories(h, kind, keys)
                    out = self.terra.complete(
                        self._prompt(h, kind, situation_json, memories),
                        agent=self.name,
                        timeout_s=timeout_s,
                        effort=effort,
                    )
                    raw = parse_last_object(out)
                decision, errs = rules.validate(
                    kind, self._translate(kind, raw, ctx), ctx
                )
                if errs:
                    errors += [f"{by}: {e}" for e in errs]
                    decision = None
                else:
                    route_used = by
            except (ModelRefused, ModelError, sandbox.SandboxError, ParseError) as e:
                errors.append(f"{by}: {type(e).__name__}: {e}")

            fb = route.get("fallback_script")
            if decision is None and fb:
                try:
                    raw = self._script(fb, kind, situation, ctx, timeout_s)
                    decision, errs = rules.validate(
                        kind, self._translate(kind, raw, ctx), ctx
                    )
                    if errs:
                        errors += [f"fallback_script: {e}" for e in errs]
                        decision = None
                    else:
                        route_used = "fallback_script"
                except sandbox.SandboxError as e:
                    errors.append(f"fallback_script: SandboxError: {e}")

        if decision is None:
            decision, route_used = (
                copy.deepcopy(rules.SAFE.get(kind, {})),
                "safe",
            )
        latency = round(time.monotonic() - t0, 3)
        self.traj.append(
            self.name,
            "decision",
            decision_kind=kind,
            harness_version=version,
            arm=arm,
            route_used=route_used,
            latency_s=latency,
            errors=errors,
            decision=decision,
            situation_summary=situation_summary(situation, situation_json),
            memory_keys=keys,
            n_memories=len(memories),
        )
        return decision, {
            "route": route_used,
            "errors": errors,
            "latency_s": latency,
            "harness_version": version,
            "arm": arm,
        }
