"""Default-off A/B G-read (11/10): the outside buyer's grade-preference opening read with its B.

11/10 06:20-07:58 market-kit.log: 31 talks open with "I want to buy <name> (<SKU>). I prefer grade C (refurbished) or B
(damaged box) with a warranty of at least 6 hours on the receipt, priced for its grade; grade A only if you have nothing
else. Please quote your cheapest unit that way." shop_stock.needs reads that as grades {A, C}: GRADES takes "grade C"
and "grade A", and the "or B (damaged box)" after the parenthesis is lost. So shop_stock.pick never offers a free
grade B unit there, even the one with the lowest floor: G8ec51935's B unit (u-7d82c4d0860b, floor 1509825, in stock from
06:42) was passed over three times for A units (floor 1706590); in two of them the buyer offered 1546976 and 1639776,
above the B floor, below the A one, and canceled. review/W-20261109-2040-AX셀러전략/report.md §10-10.

Treatment (arm B): for that exact sentence only, pick also allows grade B (shop_stock.pick add_grades), so the unit
with the lowest floor among A, B and C is offered, as for any buyer who names its grades. Quote, floor, warranty and every
guard after the pick are unchanged; the B unit is priced as a B (profit_price grade_break). Arm A: the original path.

Eligible: only the opening as the outside buyer writes it, 33 of 33 in market-kit.log(.1) 11/10 08:49-13:41:
"I want to buy <name> (<SKU>). <the sentence> Please quote your cheapest unit that way." Any other text, a grade word in
the name, or anything before or after it (e.g. "No damaged box units") keeps the original path (independent review
889d594 M3).

Randomization unit: the task, by sha256(salt|study|version|task) against share, journaled (event "assigned") before the
pick, so every eligible opening is in the ITT list whatever is in stock; a failed write keeps the original path.
"selected" records the unit picked and, in both arms, the unit each arm's pick gives (unit_a/unit_b, switch): only a
talk with switch true can differ between the arms; both picks read one units() snapshot. "no_quote" (reason selection,
warranty, unit_unavailable) ends a talk before a quote (M1); "outcome" rows carry seller.remember's result (agreed,
deal, unpaid, canceled, rejected, ...; a task can have several, the last one counts) and quoted (the quote went out:
seller sets deal["grade_read_quoted"] right after it), so a buyer's rejection after a quote is never read as a talk with
no quote, and an outcome with quoted false (an error between self.open and the quote) is no quote. An assigned task with
neither (an error after the pick) is an unknown outcome, never a loss or a non-sale. Rows carry the
other trials' arms (others) for the cross tables; payment and refunds are joined from the ledger by task and unit.
Window: [start_at, stop_at) with stop_at - start_at <= 12 h (make_config writes 9-12 h), else the original path; NaN,
inf, bool, a huge int, a missing field, a start in the future and the stop itself are off. Switch (GRADE_READ_CONFIG
overrides): {study, salt, version, epoch, enabled, mode "ab"|"adopted", share, start_at, stop_at}.
Overlap: a talk moved to a B unit leaves the grade A only trials (floor_margin, a_open) and enters goods_margin's SKU x
grade B cell; for furniture it would enter a grade-break trial (GBT) unit: none is on after 11/10 13:00, and a new GBT
window inside this one is analysed together (rows carry the unit and its grade). The pick ranks by today's floor, before
any of those trials moves a price, so which unit is quoted never depends on their arms; while a GBT window is open
(price.grade_break_trial, or a setting that cannot be read) every opening keeps the original path and gets no row, since
floor_rank does not see the GBT b (independent review 8a694a5 M).
Switch: .market/grade_read/config.json
"""
import hashlib
import math
import os
from pathlib import Path
import re
import time

import profit_price
import shelf_jev_skip
import shop_stock

MAX_WINDOW = 12 * 3600
MIN_HOURS, MAX_HOURS = 9, 12
MAX_TEXT = 500           # seller.MAX_TEXT: pick reads text[:500], so an eligible opening is read whole by both
SENTENCE = re.compile(r"I prefer grade C \(refurbished\) or B \(damaged box\) with a warranty of at least "
                      r"[0-9]{1,6}(?:\.[0-9]{1,4})? hours on the receipt, priced for its grade; grade A only if you have "
                      r"nothing else\.")
FRAME = re.compile(r"I want to buy (?P<name>[^\n]+?) \((?P<sku>G[0-9a-f]{8})\)\. (?P<sentence>I prefer grade C .+?) "
                   r"Please quote your cheapest unit that way\.")
SKU_ANYWHERE = re.compile(r"(?i)g[0-9a-f]{8}")     # what pick matches (the SKU lowercased, anywhere in the text)
NAME_GRADE_WORDS = re.compile(r"(?<![A-Za-z])[ABCabcＡＢＣａｂｃ](?![A-Za-z])|싫|제외|말고|빼고|불가|사절|파손|훼손|하자|리퍼|중고|새상품|"
                              r"(?i:\bgrades?\b|damaged|refurbished|warranty|\bonly\b|\bnot?\b|without|avoid|except)")
MORE_TRIALS = ("floor_margin", "a_open", "goods_margin", "food_margin", "shelf_jev", "warranty_fact")


def extra_grades(text):
    """{"B"} when the opening is the exact sentence and the parser read it as {A, C}; else None."""
    if not isinstance(text, str) or len(text) > MAX_TEXT or any(c in text for c in "{}\""):
        return None
    if len(SENTENCE.findall(text)) != 1:
        return None
    m = FRAME.fullmatch(text)
    if m is None or not SENTENCE.fullmatch(m["sentence"]) or NAME_GRADE_WORDS.search(m["name"]):
        return None
    if len(SKU_ANYWHERE.findall(text)) != 1 or shop_stock.needs(text)["grades"] != {"A", "C"}:
        return None
    return {"B"}


class Trial(shelf_jev_skip.Trial):
    def __init__(self, root=None):
        super().__init__(root)
        self.root = self.root.parent / "grade_read"

    def _path(self):
        return Path(os.path.abspath(os.environ.get("GRADE_READ_CONFIG") or self.root / "config.json"))

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

    @staticmethod
    def _gbt_open(pricing, now):
        """Whether price.grade_break_trial may be on now; anything unreadable counts as on."""
        try:
            t = profit_price.cfg_of(pricing).get("grade_break_trial")
            if t is None:
                return False
            return not profit_price.trial_setting_ok(t) or now < t["stop_at"]
        except Exception:
            return True

    def assign(self, task, text, now=None, pricing=None):
        """(assignment, grades to add to the pick) or (None, None) for the original path; never raises."""
        try:
            extra = extra_grades(text)
            if extra is None:
                return None, None
            now = time.time() if now is None else now
            cfg = self.config(now)
            if cfg is None or self._gbt_open(pricing, now):
                return None, None
            a = dict(study=cfg["study"], version=cfg["version"], epoch=cfg["epoch"], mode=cfg["mode"],
                     salt_sha256=hashlib.sha256(cfg["salt"].encode()).hexdigest(), arm=self._arm(cfg, task),
                     assigned_at=now, randomization_unit="task")
            skus = sorted(shop_stock.SKU.findall(text))
            self._write(dict(event="assigned", ts=now, task=task, **a, added=sorted(extra),
                             sku=skus[0] if len(skus) == 1 else None,
                             input_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest()))
            return a, (extra if a["arm"] in ("B", "adopted") else None)
        except Exception:
            return None, None

    def selected(self, task, a, unit, miss, now=None, alt=None):
        """The unit picked (unit, grade) and, from alt (the other arm's pick on the same stock), the unit each arm gives."""
        if not isinstance(a, dict) or "arm" not in a:
            return
        try:
            u = unit if isinstance(unit, dict) else {}
            o = alt if isinstance(alt, dict) else {}
            ua, ub = (o, u) if a["arm"] in ("B", "adopted") else (u, o)
            self._write(dict(event="selected", ts=time.time() if now is None else now, task=task, study=a["study"],
                             version=a["version"], arm=a["arm"], mode=a["mode"], unit=u.get("unit"), sku=u.get("sku"),
                             grade=u.get("grade"), miss=miss if isinstance(miss, str) else None,
                             unit_a=ua.get("unit"), grade_a=ua.get("grade"), cost_a=ua.get("cost"),
                             unit_b=ub.get("unit"), grade_b=ub.get("grade"), cost_b=ub.get("cost"),
                             switch=ua.get("unit") != ub.get("unit")))
        except Exception:
            pass

    def no_quote(self, task, a, reason, unit, now=None):
        """The talk ended before a quote (selection, warranty, unit_unavailable); never raises."""
        if not isinstance(a, dict) or "arm" not in a:
            return
        try:
            u = unit if isinstance(unit, dict) else {}
            self._write(dict(event="no_quote", ts=time.time() if now is None else now, task=task, study=a["study"],
                             version=a["version"], arm=a["arm"], mode=a["mode"], reason=reason, unit=u.get("unit"),
                             sku=u.get("sku"), grade=u.get("grade")))
        except Exception:
            pass

    def event(self, task, deal, result, price=None, now=None):
        a = deal.get("grade_read") if isinstance(deal, dict) else None
        if not isinstance(a, dict) or "arm" not in a:
            return
        try:
            self._write(dict(event="outcome", result=result, quoted=deal.get("grade_read_quoted") is True,
                             ts=time.time() if now is None else now, task=task,
                             study=a["study"], version=a["version"], arm=a["arm"], mode=a["mode"], price=price, sku=deal.get("id"),
                             unit=deal.get("unit"), kind=deal.get("kind"), rounds=deal.get("rounds"),
                             others=self._others(deal)))
        except Exception:
            pass


def make_config(start_at, hours, study):
    """The switch for one finite window [start_at, start_at + hours h), 9 <= hours <= 12; ValueError for anything else."""
    def num(v):
        return type(v) in (int, float) and math.isfinite(v)
    try:
        ok = num(start_at) and start_at > 0 and num(hours) and MIN_HOURS <= hours <= MAX_HOURS
    except OverflowError:
        ok = False
    if not ok or not isinstance(study, str) or not study.strip():
        raise ValueError("start_at > 0 and 9 <= hours <= 12 (finite numbers), and a study id")
    return dict(study=study, salt=study + "-salt", version=1, epoch=start_at, enabled=True, mode="ab", share=0.5,
                start_at=start_at, stop_at=start_at + hours * 3600)
