# v121 (성수님 결정 CoS 전달 17:40): shared LLM plumbing for RLPO (proposer / approver), the meta-harness policy editor
# and the memory op judge. Every call goes through llm.live_v120c.Live.call -> llm.caps_v4.OpsCaps (v121b auto-OFF per
# role: usage-limit / 429 / auth = global, model / timeout / 5xx / 3x other = that role only; no count cap) -> llm.codex_client_v4.CodexClient (live counter,
# model from llm_config.json, gpt-6.1-terra). LLM OFF or any failure -> LLMUnavailable / None: the caller keeps its
# rule or no-op path. Logs keep shas, sizes and numbers only, never prompt or reply text.
#
#   llm = RoleLLM(live, "rlpo_prop")       # .model, .complete(prompt, max_tokens=800) -> {text, tokens_in, tokens_out}
#   DecisionLog(path).append(row)          # append-only JSONL, ts added, text fields refused
#   policy_gate.check_writer(writer, target, live_policy)   # who may write the live policy.json
import hashlib
import json
import os
import time
from pathlib import Path

from llm.caps_v4 import est_tokens

HERE = Path(__file__).resolve().parent.parent


class LLMUnavailable(RuntimeError):
    # str(e) is a short code-made reason (never the CLI output): "llm off", "caps:<reason> [<kind>]"
    pass


def sha(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class RoleLLM:
    # Adapter with the rlpo FakeLLM interface. The error text is code-made, so the rlpo breaker classify() sees the
    # caps_v4 kind words (quota / auth / model / timeout / 5xx) and no CLI output reaches any rlpo log.
    KIND_WORD = {"quota": "quota 429", "auth": "auth 401", "model": "model not found", "timeout": "timeout",
                 "server": "5xx", "other": "other"}

    def __init__(self, live, role):
        self.live, self.role = live, role
        self.model = getattr(live, "model", None)
        # v121b: a new session per call = codex exec --ephemeral (fixed argv in codex_client_v4, not a setting)
        try:
            from llm.codex_client_v4 import FIXED_ARGS
            self.fresh_session = "--ephemeral" in FIXED_ARGS
        except Exception:                                # noqa: BLE001
            self.fresh_session = False

    def available(self):
        # live ON (config + caps + verified model) and this role breaker + the global breaker closed (v121b per role)
        try:
            if self.live is None or not getattr(self.live, "on", False):
                return False
            caps = getattr(self.live, "caps", None)
            if caps is None:
                return True
            if hasattr(caps, "is_open"):
                return not caps.is_open(self.role, time.time())
            return not caps.breaker.is_open(time.time())
        except Exception:                                # noqa: BLE001
            return False

    def complete(self, prompt, max_tokens=800):
        if self.live is None or not getattr(self.live, "on", False):
            raise LLMUnavailable("llm off")
        out, st = self.live.call(self.role, prompt, max_tokens)
        if out is None:
            kind = self.live.last_kind() if hasattr(self.live, "last_kind") else None
            word = self.KIND_WORD.get(kind, "")
            raise LLMUnavailable(f"caps:{str(st).split(chr(58))[0][:40]} {word}".strip())
        return {"text": out, "tokens_in": est_tokens(prompt), "tokens_out": est_tokens(out)}


TEXT_KEYS = frozenset({"prompt", "reply", "text", "rationale", "evidence_text", "raw"})


class DecisionLog:
    # append-only decision log; rows with free-text keys are refused (only shas / numbers / short codes)
    def __init__(self, path):
        self.path = Path(path)

    def append(self, row):
        bad = TEXT_KEYS & set(row)
        if bad:
            raise ValueError(f"decision log refuses text fields {sorted(bad)}")
        r = dict(row)
        r.setdefault("ts", round(time.time(), 3))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        return r

    def rows(self):
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]


class PolicyWriteRefused(RuntimeError):
    pass


class policy_gate:
    # v121: the live policy.json may be written only by the RLPO path and by an approved deploy. Every attempt
    # (allowed or refused) is one audit row. Integrity check in code, not a security boundary (box agents and
    # humans can still edit files; the RLPO C2 check catches an unregistered edit).
    ALLOWED_WRITERS = frozenset({"rlpo", "deploy"})
    AUDIT = HERE / "state" / "policy_audit.jsonl"

    @staticmethod
    def live_policy():
        return Path(os.environ.get("R3_POLICY") or HERE / "policy.json").expanduser()

    @classmethod
    def is_live(cls, target, live_policy=None):
        lp = Path(live_policy) if live_policy is not None else cls.live_policy()
        try:
            return os.path.realpath(target) == os.path.realpath(lp)
        except OSError:
            return True

    @classmethod
    def check_writer(cls, writer, target, live_policy=None, audit=None, **meta):
        live = cls.is_live(target, live_policy)
        ok = (not live) or writer in cls.ALLOWED_WRITERS
        row = {"writer": writer, "target": str(target), "live": live, "allowed": ok}
        row.update({k: v for k, v in meta.items() if k not in TEXT_KEYS})
        DecisionLog(audit or cls.AUDIT).append(row)
        if not ok:
            raise PolicyWriteRefused(f"writer {writer!r} may not write the live policy {target}")
        return True
