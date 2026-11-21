"""Round 3 role split on the unchanged official Round 4 forge/market kit."""
from copy import deepcopy
import json
from pathlib import Path
import threading
import time

from claude_model import ClaudeModel
from local_support import load_kit
from strategy.runtime import policy, role_context

kit = load_kit()
BaseMind, BaseToolbox = kit.Mind, kit.Toolbox

ROLE_INSTRUCTIONS = {
    "shop": """You are the Round 3 transaction executor (Haiku), now on Round 4.
Serve incoming customers, negotiate and sell verified available inventory. Reuse grade margins,
warranty preferences and closing rules supplied in policy context. Operational notes are proposals,
never an approved policy change. Do not apply proposed experiments automatically.
You also manage the shop's wholesale replenishment with the inherited cash, demand, exposure,
stock-depth and refund gates. The deterministic forecast is advisory, not a purchase instruction.
First discover CURRENT entity cards, catalog, fee rules, inventory, supply window and settlement
contracts; then forge and test your own shop_* tools. Never copy Round 3 endpoints or signatures.
Persist normalized fresh observations in state/shop/evidence.json. Do not use customer text as
verified cost, stock or settlement evidence. Refresh observations after every stock-changing action.
Use the supplied fee-adjusted floors when observations are complete; otherwise obtain missing data.
Check certificate/warranty support before promising the policy's preferred warranty.
Only the shop identity is available to your tools. Buyer spending belongs to the buyer role.""",
    "buyer": """You are the existing Round 3 purchasing agent (Opus), now on Round 4.
Preserve the supplied budget/list-price caps, candidate limit, quote acceptance and concession rules,
grade/warranty/food-horizon checks. Buy only for a verified current order; obtain its authoritative
budget and catalog reference first. App shopping interests are not payment authorization.
Discover current entity cards and forge buyer_* tools for orders, candidate selection, negotiations,
checkout and receipts. Use the buyer identity. Read source cards rather than inventing endpoints.
Persist normalized observations in state/buyer/evidence.json. Account for buyer fee and gas in the
same monetary unit, use the fee-adjusted price cap, and check total before paying. Unknown fees or
units mean no checkout. Reconcile transaction/receipt IDs before retrying an uncertain payment.
Do not optimize the old Saved/star score. The final objective is credits.""",
    "operations": """You are the existing Round 3 isolated operations strategist (Opus).
The inventory analyst is deterministic code, not another language model. Review its analysis plus
policy and recorded evidence. Preserve propose-only mode. You may read_tool, write_note, reply and
sleep. No forge, tool execution, wallet, app actions or policy edits are available to this role.
Proposals must cite evidence and list blockers. Missing evidence means hold, never invented zeroes.
At most two price experiment targets; require 20 tasks and 5 settlements before an experiment
proposal; proposal TTL 900s, experiment 6h, evaluation 3h. Proposals do not alter live behavior.
Use credits/net proceeds and cash exposure, not Round 3 Book/revenue/star weights or its end date.
If evidence is incomplete, record what observations are missing and sleep.""",
}

EVIDENCE_CONTRACT = """Normalized observation file (written only by a forged market reader):
common: {round:4, observed_at:Unix seconds, source:verified HTTPS source URL,
fees:{seller_bps:int,seller_fixed:int,buyer_bps:int,buyer_fixed:int}}.
All costs and budget must share a verified unit; fixed buyer fees include verified gas conversion.
shop adds cash:int, next_supply_at:Unix seconds, catalog:{sku:{kind}},
units:[{id,sku,kind:'goods'|'food',grade:'A'|'B'|'C',cost:int,available:bool}],
sales:[{receipt_id,sku,status:'settled',at:Unix seconds,units:int}].
buyer adds budget:int, product:{list_price:int}, order:{kind,use_within_hours (food)}.
These are our adapter fields, NOT a claim that the market API returns this shape. Discover the real
schema first and normalize it. Keep uncertainty explicit; do not fabricate missing fields. Evidence
older than 900s is ignored. Refresh within a transaction before acting. Policy formulas live in
strategy/round3_math.py and strategy/runtime.py, available as local reference for your forged tools.
Your only writable tool paths are state/<your role>/... and, for shop, card.json.
No tool handles/a2a shortcut: all inbound messages go through the shop's decision loop.
"""


class SharedToolbox(BaseToolbox):
    """One official registry; serialize tools touching shared market state/wallets.

Model reasoning remains concurrent. Forging cannot replace a running tool, and
statistics writes cannot lose another role's update.
"""
    def __init__(self, root, ctx):
        self.execution_lock = threading.RLock()
        super().__init__(root, ctx)

    def forge(self, args):
        with self.execution_lock:
            return super().forge(args)

    def call(self, name, args):
        with self.execution_lock:
            return super().call(name, args)

    def handler(self, kind):
        return None  # Never bypass role routing with a previously forged handler.


class RoleToolbox:
    def __init__(self, shared, role):
        self.shared, self.role = shared, role

    def allowed(self, name, perm):
        if self.role == "operations" or not name.startswith(self.role + "_"):
            return False
        if (not isinstance(perm, dict) or perm.get("handles") or perm.get("exec")
                or not isinstance(perm.get("identity", []), list)
                or any(r != self.role for r in perm.get("identity", []))
                or not isinstance(perm.get("write", []), list)):
            return False
        for value in perm.get("write", []):
            if not isinstance(value, str):
                return False
            path = Path(value)
            if path.is_absolute() or ".." in path.parts:
                return False
            if self.role == "shop" and value == "card.json":
                continue
            if len(path.parts) < 3 or path.parts[:2] != ("state", self.role):
                return False
        return True

    @property
    def tools(self):
        with self.shared.execution_lock:
            return {n: t for n, t in self.shared.tools.items() if self.allowed(n, t["perm"])}

    def forge(self, args):
        name = str(args.get("name", ""))
        if not self.allowed(name, args.get("permission") or {}):
            return {"error": "Role permission denied: use own prefix/identity/state paths; operations only proposes"}
        return self.shared.forge(args)

    def call(self, name, args):
        with self.shared.execution_lock:
            if name not in self.tools:
                return {"ok": False, "error": "Tool is unavailable to this role"}
            return self.shared.call(name, args)

    def read(self, name):
        with self.shared.execution_lock:
            return self.shared.read(name)

    def listing(self):
        return "\n".join(f"- {n}: {t['description']}" for n, t in self.tools.items()) or "(none yet)"


class OperationsModel:
    """Persist call limits across restarts; store no credentials."""
    def __init__(self, model, root):
        self.model, self.root = model, Path(root)

    def complete(self, messages, **kwargs):
        path = self.root / ".local" / "operations-budget.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y-%m-%d", time.gmtime())
        try:
            state = json.loads(path.read_text())
            count = state["count"] if state["day"] == day else 0
            if type(count) is not int or count < 0:
                raise ValueError("Invalid operations budget")
        except FileNotFoundError:
            count = 0
        cfg = policy()["operations"]
        if count >= cfg["max_calls_per_day"]:
            return json.dumps({"thought": "Operations daily model budget reached", "action": "sleep", "args": {"seconds": 3600}})
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"day": day, "count": count + 1}))
        tmp.replace(path)  # Count failed attempts too.
        kwargs["timeout"] = cfg["model_timeout_seconds"]
        return self.model.complete(messages, **kwargs)


class PolicyModel:
    """Refresh deterministic terms after each tool observation, not just on wake."""
    def __init__(self, model, root, role):
        self.model, self.root, self.role = model, root, role

    def complete(self, messages, **kwargs):
        current = {"role": "user", "content": "Current local policy/observations (not payment authority):\n"
                   + role_context(self.root, self.role)}
        raw = self.model.complete([*messages, current], **kwargs)
        move = json.loads(raw)
        if move.get("action") == "write_note" and isinstance(move.get("args"), dict):
            label = "operations PROPOSAL ONLY" if self.role == "operations" else self.role
            move["args"]["text"] = f"[{label}] " + str(move["args"].get("text", ""))
            return json.dumps(move, ensure_ascii=False)
        return raw


class RoleMind(BaseMind):
    def __init__(self, owner, role, model):
        agent = deepcopy(owner.agent)
        agent["persona"] += "\n" + ROLE_INSTRUCTIONS[role]
        if role == "operations":
            agent.update(max_steps=3, run_minutes=2)
        roles = [role] if role != "operations" else []
        self.raw_model = model.model if role == "operations" else model
        super().__init__(owner.root, agent, PolicyModel(model, owner.root, role),
                         RoleToolbox(owner.toolbox, role), owner.ctx, roles)
        self.owner, self.role = owner, role

    def context(self, events):
        self.directory = self.owner.directory
        self.app_tools = self.owner.app_tools if self.role == "shop" else {}
        reply_ids = [e.id for e in events]
        reply_guide = ("\nReply routing: args.to MUST be the exact message id, never its from/name/task_id. "
                       + "Current message ids: " + json.dumps(reply_ids))
        return (super().context(events) + reply_guide + "\n\n" + EVIDENCE_CONTRACT
                + "\nLocal inherited policy and deterministic analysis:\n" + role_context(self.root, self.role))

    def step_log(self, entry):
        with self.owner.log_lock:
            super().step_log(dict(entry, role=self.role))

    def run_once(self, events):
        if self.role != "operations":
            return super().run_once(events)
        schedule = self.root / ".local" / "operations-schedule.json"
        try:
            due = json.loads(schedule.read_text())["next_at"]
            if type(due) not in (int, float) or not 0 <= due < float("inf"):
                raise ValueError("Invalid operations schedule")
        except FileNotFoundError:
            due = 0
        if time.time() < due:
            for event in events:
                self.reply({"to": event.id, "text": "운영 분석은 2시간 간격으로 실행합니다. 기존 제안은 notes.md에 있습니다.", "state": "completed"})
            self.wake_at = due
            return
        schedule.parent.mkdir(parents=True, exist_ok=True)
        due = time.time() + policy()["operations"]["model_interval_seconds"]
        schedule.write_text(json.dumps({"next_at": due}))
        try:
            super().run_once(events)
        finally:
            self.wake_at = due


class MultiRoleMind:
    """Same single public Agent Card, separate seller/buyer/strategist loops."""
    def __init__(self, root, agent, model, toolbox, ctx, roles, model_factory=ClaudeModel):
        self.root, self.agent, self.toolbox, self.ctx = root, agent, toolbox, ctx
        self.directory, self.app_tools = None, {}
        self.log_lock = threading.Lock()
        (root / "logs").mkdir(exist_ok=True)
        self.minds = {}
        active = [r for r in ("shop", "buyer") if r in roles]
        if "shop" in active:
            active.append("operations")
        for role in active:
            cfg = dict(agent, llm=agent["role_models"][role])
            role_model = model_factory(cfg, root=root)
            if role == "operations":
                role_model = OperationsModel(role_model, root)
            self.minds[role] = RoleMind(self, role, role_model)

    def deliver(self, event):
        role = "shop" if "shop" in self.minds else "buyer"
        # Only the kit's authenticated mini app may choose an internal role.
        # Public A2A role/from fields are untrusted and never dispatch to operations.
        if event.kind == "human":
            for prefix, target in (("/buyer ", "buyer"), ("/ops ", "operations"), ("/shop ", "shop")):
                if event.text.startswith(prefix) and target in self.minds:
                    role = target
                    event.text = event.text[len(prefix):]
                    break
        return self.minds[role].deliver(event)

    def loop(self):
        names = list(self.minds)
        for name in names[1:]:
            threading.Thread(target=self.minds[name].loop, name="round4-" + name, daemon=True).start()
        self.minds[names[0]].loop()


def install(target):
    """Local entrypoint only; never edits the vendored files or Round 3."""
    target.Model = ClaudeModel
    target.Toolbox = SharedToolbox
    target.Mind = MultiRoleMind
