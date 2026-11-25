"""Default-off A/B W-fact (11/10): the exact goods warranty question answered with the warranty the shop already put on
this talk, instead of the harness.

11/10 06:20-07:00 market-kit.log: a buyer asks "Does this unit come with a warranty of at least 6 hours written on the
receipt? I only buy goods with one. ..." after our first quote. That quote already said "This unit comes with a 72 h
warranty" (Seller.warranty_terms, logged WARRANTY 72 h) and the receipt carries it (stock_receipt warranty_hours). The
question has no offer in it, so the turn goes to the harness, whose text is then dropped: the buyer reads only "Please
tell me the price you offer. My price: <ask>." The question is never answered (the harness itself, with no warranty in
decide_turn's prompt, wrote "I can't confirm that warranty" or "I can only offer this unit" in 13 of 14). All 14 talks
were canceled. review/W-20261109-2040-AX셀러전략/report.md §10-9.

Treatment (arm B): "Yes. This unit comes with a <h> h warranty written on the signed receipt: if it breaks within <h> h,
you get the full price back. My price: <ask>.", a counter at the current ask; no Jev and no harness for that turn. It
answers only for goods whose talk carries a warranty (deal["warranty"], set by warranty_terms) of at least the hours
asked; a talk sold as is (seller_warranty_trial treatment) or with less is left alone in both arms. So W-fact B only
touches seller_warranty_trial's control talks: do not run the two windows together unless they are analysed together
(rows carry others.warranty_trial); grade-warranty-v1 is enabled false and ended 11/09. Arm A: the original
path, unchanged. Every price guard after the turn is unchanged. The question is goods only; S-shelf (shelf_fact) answers
food only, so no turn is in both.

Randomization unit: the task, by sha256(salt|study|version|task) against share; the arm is fixed at the first eligible
turn and journaled (event "turn") before a B answer is given (ITT over tasks that reached an eligible turn); a failed
write keeps the original path. Outcome rows (seller.remember: deal, unpaid, canceled, ...) for every assigned task.

Window: start_at < stop_at and stop_at - start_at <= 20 h; outside it, or absent/invalid/disabled configuration: the
original path. The switch (WARRANTY_FACT_CONFIG overrides) has the shelf_jev_skip format
{study, salt, version, epoch, enabled, mode "ab", share, start_at, stop_at}; mode "adopted" answers every eligible turn.
Switch: .market/warranty_fact/config.json
"""
import hashlib
import math
import os
from pathlib import Path
import re
import time

import shelf_jev_skip

MAX_WINDOW = 20 * 3600
QUESTION = re.compile(r"Does this unit come with a warranty of at least ([0-9]{1,6}(?:\.[0-9]{1,4})?) hours written on "
                      r"the receipt\? I only buy goods with one\. If you have a unit with it, offer that one and its "
                      r"price\.")
# assignments on the deal besides shelf_jev_skip.OTHER_TRIALS, kept on each row for the cross table
MORE_TRIALS = ("floor_margin", "a_open", "food_margin", "shelf_jev")


def factual_answer(text, deal):
    """{message, hours, required} for the exact question when this goods talk carries enough warranty, else None."""
    if not isinstance(text, str) or len(text) > shelf_jev_skip.MAX_TEXT:
        return None
    m = QUESTION.fullmatch(text.strip())
    if not m or deal.get("kind") != "goods" or not deal.get("unit"):
        return None
    if (deal.get("warranty_trial") or {}).get("arm") == "treatment":
        return None
    hours = deal.get("warranty")
    if type(hours) not in (int, float) or not math.isfinite(hours) or hours <= 0:
        return None
    if type(deal.get("ask")) is not int or deal["ask"] <= 0:
        return None
    required = float(m[1])
    if not math.isfinite(required) or hours < required:
        return None
    h = f"{hours:g}"
    return dict(message=f"Yes. This unit comes with a {h} h warranty written on the signed receipt: if it breaks within "
                        f"{h} h, you get the full price back. My price: {deal['ask']}.",
                hours=float(hours), required=required)


class Trial(shelf_jev_skip.Trial):
    def __init__(self, root=None):
        super().__init__(root)
        self.root = self.root.parent / "warranty_fact"

    def _path(self):
        return Path(os.path.abspath(os.environ.get("WARRANTY_FACT_CONFIG") or self.root / "config.json"))

    @staticmethod
    def _others(deal):
        out = shelf_jev_skip.Trial._others(deal)
        for k in MORE_TRIALS:
            v = deal.get(k)
            if isinstance(v, dict):
                out[k] = {f: v[f] for f in shelf_jev_skip.OTHER_FIELDS if isinstance(v.get(f), (str, int, float, bool))}
        return out

    def config(self, now=None):
        cfg = super().config(now)
        if cfg is None or cfg["stop_at"] - cfg["start_at"] > MAX_WINDOW:
            return None
        return cfg

    def answer(self, task, text, deal, now=None):
        """The B line for this turn, or None (the original path); never raises."""
        try:
            fact = factual_answer(text, deal)
            if fact is None:
                return None
            now = time.time() if now is None else now
            cfg = self.config(now)
            if cfg is None:
                return None
            salt_sha = hashlib.sha256(cfg["salt"].encode()).hexdigest()
            a = deal.get("warranty_fact")
            if not (isinstance(a, dict) and a.get("study") == cfg["study"] and a.get("version") == cfg["version"]
                    and a.get("mode") == cfg["mode"] and a.get("epoch") == cfg["epoch"]
                    and a.get("salt_sha256") == salt_sha):
                a = dict(study=cfg["study"], version=cfg["version"], epoch=cfg["epoch"], mode=cfg["mode"],
                         salt_sha256=salt_sha, arm=self._arm(cfg, task), assigned_at=now, randomization_unit="task")
            treated = a["arm"] in ("B", "adopted")
            self._write(dict(event="turn", ts=now, task=task, study=a["study"], version=a["version"],
                             epoch=a["epoch"], mode=a["mode"], arm=a["arm"], assigned_at=a["assigned_at"],
                             randomization_unit="task", salt_sha256=a["salt_sha256"], answered=treated,
                             warranty_hours=fact["hours"], required_hours=fact["required"],
                             round=deal.get("rounds"), ask=deal.get("ask"), reserve=deal.get("reserve"),
                             sku=deal.get("id"), unit=deal.get("unit"), kind=deal.get("kind"),
                             input_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                             answer_sha256=hashlib.sha256(fact["message"].encode("utf-8")).hexdigest() if treated else None,
                             others=self._others(deal)))
            deal["warranty_fact"] = a
            return fact["message"] if treated else None
        except Exception:
            return None

    def event(self, task, deal, result, price=None, now=None):
        a = deal.get("warranty_fact") or {}
        if not a:
            return
        try:
            self._write(dict(event=result, ts=time.time() if now is None else now, task=task, study=a["study"],
                             version=a["version"], arm=a["arm"], mode=a["mode"], price=price, sku=deal.get("id"),
                             unit=deal.get("unit"), kind=deal.get("kind"), rounds=deal.get("rounds"),
                             others=self._others(deal)))
        except Exception:
            pass
