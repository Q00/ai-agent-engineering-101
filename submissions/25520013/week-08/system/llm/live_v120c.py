"""Model glue for the seller hook: price-parse assist and policy editor calls. OFF by default.

ON only when BOTH llm_caps_config_v4.json enabled=true AND llm_config.json real_calls_enabled=true and live_ops=true.
parse (interpreter assist): asked only when the rule parser offer_in() returns None; 8 s.
GPT 6.1 Terra; count caps = settings (llm_caps_config_v4.json "caps"), default UNLIMITED;
usage-limit / 429 / auth error -> breaker auto-OFF (rule path keeps running); calls + latency logged.
editor: one call per cycle through OpsCaps (min interval 4 h, 6/d); the caller checks the
result against the policy bounds before anything is applied. Any error -> rule path (offer None) / skip cycle."""
import json
from pathlib import Path

from llm import caps_v4 as CAPS
from llm import codex_client_v4 as C
from llm import parse_assist as PA

HERE = Path(__file__).resolve().parent.parent
STATE = HERE / "state"
LLM_ROLES = tuple(CAPS.ROLES)          # parse, editor, rlpo_prop, rlpo_appr, memory


def _cfg_on(base=HERE):
    try:
        caps = json.loads((Path(base) / "llm_caps_config_v4.json").read_text(encoding="utf-8"))
        cfg = json.loads((Path(base) / "llm_config.json").read_text(encoding="utf-8"))
    except Exception:                                    # noqa: BLE001
        return False
    # hard gate: model_verified must equal model
    return (caps.get("enabled") is True and cfg.get("real_calls_enabled") is True and cfg.get("live_ops") is True
            and isinstance(cfg.get("model"), str) and cfg.get("model_verified") == cfg.get("model"))


def _stamp(base):
    out = []
    for n in ("llm_caps_config_v4.json", "llm_config.json"):
        try:
            st = (Path(base) / n).stat()
            out.append((st.st_mtime_ns, st.st_size, st.st_ino))
        except OSError:
            out.append(None)
    return tuple(out)


class Live:
    """v120c-B r2: the on/off flags are re-read by refresh() (called from Seller.refresh_policy, i.e. on every policy
    reload check between deals). Config files changed -> re-read; any error -> OFF. No process restart needed."""

    def __init__(self, base=HERE, clients=None, clock=None):
        self.base = Path(base)
        self._given = dict(clients or {})
        self._clock = clock
        self.on, self.caps, self.clients, self._stamp = False, None, {}, None
        self.refresh()

    def refresh(self):
        """-> (on, changed). Never raises; on error the result is OFF."""
        try:
            st = _stamp(self.base)
            if st == self._stamp:
                return self.on, False
            self._stamp = st
            was = self.on
            on = _cfg_on(self.base)
            if on:                                       # r3: rebuilt on every config change (caps settings)
                kw = {} if self._clock is None else {"clock": self._clock}
                capcfg = CAPS.load_config(self.base / "llm_caps_config_v4.json")
                kw["caps"] = CAPS.role_caps(capcfg)            # r3: settings caps, default unlimited; bad -> OFF
                self.caps = CAPS.OpsCaps(self.base / "state", config={"enabled": True, "usd_per_1k_tokens": capcfg.get("usd_per_1k_tokens")}, **kw)
                self.clients = dict(self._given)
                for role in LLM_ROLES:            # v121: + rlpo_prop / rlpo_appr / memory (same codex path)
                    if role not in self.clients:
                        self.clients[role] = C.CodexClient(role, config_path=self.base / "llm_config.json",
                                                           base=self.base, live=True)
            self.on = on
            return self.on, was != on
        except Exception:                                # noqa: BLE001
            self.on = False
            return False, True

    async def offer(self, text, rule_fn, deal_id, ask):
        """-> (offer or None, source). Never raises."""
        try:
            if not self.on:
                return rule_fn(text), "off"
            return await self.caps.aoffer(text, rule_fn, self.clients["parse"], PA.build_prompt, PA.parse_reply,
                                          deal_id, ask)
        except Exception as e:                           # noqa: BLE001
            return None, "fallback:" + type(e).__name__

    def editor(self, prompt, max_tokens=800):
        if not self.on:
            return None, "off"
        try:
            return self.caps.editor_call(self.clients["editor"], prompt, max_tokens)
        except Exception as e:                           # noqa: BLE001
            return None, "fallback:" + type(e).__name__

    def call(self, role, prompt, max_tokens=800):
        # v121: one call for any role through OpsCaps (v121b: per-role auto-OFF + global quota/auth; no count cap). Never raises.
        # -> (text or None, reason). None = the caller takes its rule / no-op path.
        if not self.on:
            return None, "off"
        if role not in LLM_ROLES or role not in self.clients:
            return None, "unknown_role"
        try:
            return self.caps.call(role, self.clients[role], prompt, max_tokens)
        except Exception as e:                           # noqa: BLE001
            return None, "fallback:" + type(e).__name__

    def last_kind(self):
        # kind of the last OpsCaps call (quota | auth | model | server | other | ok | timeout) or None; no text
        try:
            return (self.caps.last or {}).get("kind")
        except Exception:                                # noqa: BLE001
            return None

    @property
    def model(self):
        try:
            return json.loads((self.base / "llm_config.json").read_text(encoding="utf-8")).get("model")
        except Exception:                                # noqa: BLE001
            return None
